"""Ask Windows to trust this installation's key-less local root.

Only a root made by windows_tls.create_identity qualifies: no signing key
exists for it, and it permits only this installation's own names. Windows
shows its own confirmation for every add and removal; nothing here answers it.
Only the signed-in person's Current User store is used, never Local Machine,
elevation or policy.

`setup-tls force` replaces the identity, and Windows can still hold the root
that it replaced. windows_tls records each replaced root. A recorded root is
removed only when it has the fixed shape of this product's root and the store
holds exactly that certificate: after the person's Yes to the new root
(replaced), and at an uninstall (held_roots, forget_roots). Windows asks the
person once for each removal.

Outside what this protects: changed app code, or any other program that runs
as the same person, can ask Windows for the same confirmation itself.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from datetime import UTC, datetime, timedelta
import hashlib
import ipaddress
import os
import threading

from .windows_tls import ROOT_NAME  # The name Windows shows in its confirmation.

_PROVIDER_SYSTEM = 10              # CERT_STORE_PROV_SYSTEM_W
_ENCODING = 0x00010001             # X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
_CURRENT_USER = 0x00010000         # CERT_SYSTEM_STORE_CURRENT_USER
_READ_ONLY = 0x00008000            # CERT_STORE_READONLY_FLAG
_ADD_REPLACE_INHERIT = 5           # CERT_STORE_ADD_REPLACE_EXISTING_INHERIT_PROPERTIES
_FIND_EXISTING = 0x000D0000        # CERT_FIND_EXISTING
_CANCELLED = 1223                  # ERROR_CANCELLED: the person selected No
_DISABLE_USER_ROOTS = 0x1          # CERT_PROT_ROOT_DISABLE_CURRENT_USER_FLAG


def thumbprint(der):
    """SHA-1 thumbprint in the grouping Windows shows in its confirmation."""
    value = hashlib.sha1(der).hexdigest().upper()
    return " ".join(value[index:index + 8] for index in range(0, 40, 8))


def _shape(root):
    """Permitted names of a certificate with the fixed shape of this product's root.

    Raises ValueError for every other certificate. The shape is what
    windows_tls._new_root makes: the product's name, self-signed, a CA that
    can sign only server certificates, with critical name constraints. The
    period of validity and the names are the caller's to check.
    """
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ROOT_NAME)])
    basic = root.extensions.get_extension_for_class(x509.BasicConstraints)
    usage = root.extensions.get_extension_for_class(x509.KeyUsage).value
    purposes = root.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    constraints = root.extensions.get_extension_for_class(x509.NameConstraints)
    if (root.subject != name or root.issuer != name
            or not basic.critical or not basic.value.ca or basic.value.path_length != 0
            or not usage.key_cert_sign or list(purposes) != [ExtendedKeyUsageOID.SERVER_AUTH]
            or not constraints.critical or constraints.value.excluded_subtrees
            or not constraints.value.permitted_subtrees):
        raise ValueError("root is not this installation's local-only shape")
    root.verify_directly_issued_by(root)
    return constraints.value.permitted_subtrees


def check(root_der, leaf_der, config):
    """Raise ValueError unless this is exactly a key-less identity's root.

    This guards against a defective identity reaching the Windows store. It is
    not a defence against changed code: a certificate cannot show that nobody
    kept its signing key.
    """
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from .windows_tls import LIFETIME, local_names
    try:
        root = x509.load_der_x509_certificate(root_der)
        leaf = x509.load_der_x509_certificate(leaf_der)
        permitted = _shape(root)
        now = datetime.now(UTC)
        if (not root.not_valid_before_utc <= now < root.not_valid_after_utc
                or root.not_valid_after_utc - root.not_valid_before_utc > LIFETIME + timedelta(days=1)):
            raise ValueError("root is not valid now, or it is valid for too long")
        hosts, addresses = set(), set()
        for subtree in permitted:
            if isinstance(subtree, x509.DNSName):
                hosts.add(subtree.value)
            elif (isinstance(subtree, x509.IPAddress) and isinstance(
                    subtree.value, (ipaddress.IPv4Network, ipaddress.IPv6Network))
                    and subtree.value.prefixlen == subtree.value.max_prefixlen):
                addresses.add(subtree.value.network_address)
            else:
                raise ValueError("root permits a name form or address range that is not this installation's")
        expected_hosts, expected_addresses = local_names(config)
        if (not hosts <= set(expected_hosts)
                or not addresses <= {ipaddress.ip_address(value) for value in expected_addresses}):
            raise ValueError("root permits a name that is not this installation's")
        leaf.verify_directly_issued_by(root)
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if (leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                or len(leaf.subject) != 0
                or not set(names.get_values_for_type(x509.DNSName)) <= hosts
                or not set(names.get_values_for_type(x509.IPAddress)) <= addresses
                or len(list(names)) != len(names.get_values_for_type(x509.DNSName))
                + len(names.get_values_for_type(x509.IPAddress))):
            raise ValueError("server certificate is outside the root's permitted names")
    except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature) as error:
        raise ValueError("The local certificate is not a key-less local-only identity; "
                         "Windows was not asked to trust it") from error


def candidate(home, pin, fresh=None):
    """Return the root (DER) this installation may ask Windows to trust, else None.

    `fresh` is the root this same process just created: that exact in-memory
    certificate is returned, never a re-read copy. A later start has no such
    copy and uses the owner-only store. `pin` is the privately read server
    certificate that the running host presents.
    """
    from .deployment import load_server_config
    from .windows_tls import keyless_identity
    identity = keyless_identity(home)
    if identity is None:
        # Made by an earlier setup with a signing key on disk: never added.
        return None
    der, leaf_der = identity
    if (fresh is not None and fresh != der) or hashlib.sha256(leaf_der).digest() != pin.fingerprint:
        raise ValueError("The local certificate changed; Windows was not asked to trust it")
    check(der, leaf_der, load_server_config(home))
    return fresh if fresh is not None else der


def user_roots_disabled():
    """True when device policy tells Windows to ignore Current User roots.

    Only an early answer: it avoids a Windows question whose Yes would change
    nothing. The check after the add decides in every case.
    """
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Policies\Microsoft\SystemCertificates\Root\ProtectedRoots") as key:
            value, kind = winreg.QueryValueEx(key, "Flags")
    except OSError:
        return False
    return kind == winreg.REG_DWORD and bool(value & _DISABLE_USER_ROOTS)


def _api():
    if os.name != "nt":
        raise RuntimeError("The Windows certificate store requires native Windows.")
    from ctypes import wintypes as W
    api = C.WinDLL("crypt32", use_last_error=True)
    api.CertOpenStore.argtypes = [C.c_void_p, W.DWORD, C.c_void_p, W.DWORD, C.c_wchar_p]
    api.CertOpenStore.restype = C.c_void_p
    api.CertCloseStore.argtypes = [C.c_void_p, W.DWORD]
    api.CertCloseStore.restype = W.BOOL
    api.CertCreateCertificateContext.argtypes = [W.DWORD, C.c_char_p, W.DWORD]
    api.CertCreateCertificateContext.restype = C.c_void_p
    api.CertFreeCertificateContext.argtypes = [C.c_void_p]
    api.CertFreeCertificateContext.restype = W.BOOL
    api.CertAddCertificateContextToStore.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p]
    api.CertAddCertificateContextToStore.restype = W.BOOL
    api.CertFindCertificateInStore.argtypes = [C.c_void_p, W.DWORD, W.DWORD, W.DWORD, C.c_void_p, C.c_void_p]
    api.CertFindCertificateInStore.restype = C.c_void_p
    api.CertDeleteCertificateFromStore.argtypes = [C.c_void_p]
    api.CertDeleteCertificateFromStore.restype = W.BOOL
    return api


@contextmanager
def _open(der, store, *, write):
    """Yield (api, store handle, certificate context) for the person's own store."""
    api = _api()
    handle = api.CertOpenStore(_PROVIDER_SYSTEM, _ENCODING, None,
                               _CURRENT_USER | (0 if write else _READ_ONLY), store)
    if not handle:
        raise C.WinError(C.get_last_error())
    try:
        context = api.CertCreateCertificateContext(_ENCODING, der, len(der))
        if not context:
            raise C.WinError(C.get_last_error())
        try:
            yield api, handle, context
        finally:
            api.CertFreeCertificateContext(context)
    finally:
        api.CertCloseStore(handle, 0)


def _find(api, handle, context):
    return api.CertFindCertificateInStore(handle, _ENCODING, 0, _FIND_EXISTING, context, None)


def contains(der, *, store="ROOT"):
    """Is this exact certificate in the person's view of the store?"""
    with _open(der, store, write=False) as (api, handle, context):
        found = _find(api, handle, context)
        if found:
            api.CertFreeCertificateContext(found)
        return bool(found)


def _dialog_windows():
    """Visible dialog windows that belong to this process."""
    from ctypes import wintypes as W
    user = C.WinDLL("user32", use_last_error=True)
    visitor = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.EnumWindows.argtypes = [visitor, W.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.GetWindowThreadProcessId.restype = W.DWORD
    user.IsWindowVisible.argtypes = [W.HWND]
    user.GetClassNameW.argtypes = [W.HWND, C.c_wchar_p, C.c_int]
    process, found = os.getpid(), []

    def visit(window, _):
        owner, name = W.DWORD(), C.create_unicode_buffer(16)
        user.GetWindowThreadProcessId(window, C.byref(owner))
        if (owner.value == process and user.IsWindowVisible(window)
                and user.GetClassNameW(window, name, 16) and name.value == "#32770"):
            found.append(window)
        return True
    user.EnumWindows(visitor(visit), 0)
    return found


def _bring_forward(window):
    """Put a window above the others. Window order only; nothing answers it."""
    from ctypes import wintypes as W
    user = C.WinDLL("user32", use_last_error=True)
    user.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
    user.SetForegroundWindow.argtypes = [W.HWND]
    user.SetWindowPos(window, W.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002)  # Topmost; keep size and place.
    user.SetForegroundWindow(window)


def _raise_dialogs(done):
    """Keep this process's Windows confirmation in front of other windows.

    Windows may open it behind them, where the person sees only a taskbar
    button. The person still answers it.
    """
    raised = set()
    try:
        while not done.wait(0.2):
            for window in _dialog_windows():
                if window not in raised:
                    raised.add(window)
                    _bring_forward(window)
    except Exception:  # Window order is a convenience; never fail the trust step for it.
        return


def add_root(der, *, store="ROOT"):
    """Add the root to the person's own trusted roots; Windows asks them first.

    Returns `present` (already there, no question), `added`, `declined` (the
    person selected No) or `blocked` (Windows refused or did not keep it).
    The call waits while the Windows confirmation is open.
    """
    done = threading.Event()
    helper = threading.Thread(target=_raise_dialogs, args=(done,), daemon=True)
    try:
        with _open(der, store, write=True) as (api, handle, context):
            found = _find(api, handle, context)
            if found:
                api.CertFreeCertificateContext(found)
                return "present"
            helper.start()
            if api.CertAddCertificateContextToStore(handle, context, _ADD_REPLACE_INHERIT, None):
                outcome = "added"
            else:
                outcome = "declined" if C.get_last_error() == _CANCELLED else "blocked"
    except OSError:
        outcome = "blocked"
    finally:
        done.set()
        if helper.is_alive():
            helper.join(2)
    try:
        # Windows said yes; require that the store really holds it now.
        if outcome == "added" and not contains(der, store=store):
            return "blocked"
    except OSError:
        return "blocked"
    return outcome


def remove_root(der, *, store="ROOT"):
    """Remove the root from the person's own trusted roots; Windows asks them first.

    Returns `absent`, `removed`, `declined` or `blocked`. The default button of
    the Windows question is No, so `declined` is an ordinary outcome: the root
    stays, and it is inert because no key can sign under it.
    """
    done = threading.Event()
    helper = threading.Thread(target=_raise_dialogs, args=(done,), daemon=True)
    try:
        with _open(der, store, write=True) as (api, handle, context):
            found = _find(api, handle, context)
            if not found:
                return "absent"
            helper.start()
            # Windows frees the found context whether or not it deletes it.
            if api.CertDeleteCertificateFromStore(found):
                outcome = "removed"
            else:
                outcome = "declined" if C.get_last_error() == _CANCELLED else "blocked"
    except OSError:
        outcome = "blocked"
    finally:
        done.set()
        if helper.is_alive():
            helper.join(2)
    try:
        if outcome == "removed" and contains(der, store=store):
            return "blocked"
    except OSError:
        return "blocked"
    return outcome


def _own_shape(der):
    """Does this certificate have the fixed shape of this product's root?

    For a replaced root. Its period of validity and its names are not
    compared: it can be expired, and it names an earlier configuration.
    """
    from cryptography import x509
    try:
        _shape(x509.load_der_x509_certificate(der))
    except Exception:
        # Every failure to read it as this shape means that it is not this
        # shape (a signature of another kind, a repeated extension, ...). The
        # answer False only leaves a certificate where it is.
        return False
    return True


def replaced(home, current=None, *, store="ROOT"):
    """Roots (DER) that this installation replaced and that Windows still holds for this person.

    Only `setup-tls force` writes the record, and the record only names
    candidates. One is returned only when it has the fixed shape of this
    product's root and the store holds exactly that certificate. `current`
    is never returned: a `force` that stopped part-way records the root that
    is still in use. Oldest first. Read-only; Windows asks nothing here.
    """
    from .windows_tls import replaced_roots
    return [der for der in replaced_roots(home)
            if der != current and _own_shape(der) and contains(der, store=store)]


def held_roots(home, *, store="ROOT"):
    """This installation's roots (DER) that Windows holds for this person. Never raises.

    The current root of a key-less identity comes first, then the replaced
    ones. An identity with a signing key on disk added nothing and is not
    listed. What cannot be read is left out: nothing is removed on a guess.
    """
    from .windows_tls import keyless_identity
    current, found = None, []
    try:
        identity = keyless_identity(home)
        if identity is not None:
            current = identity[0]
            if contains(current, store=store):
                found.append(current)
    except (OSError, ValueError, RuntimeError):
        pass
    try:
        found += replaced(home, current, store=store)
    except (OSError, ValueError, RuntimeError):
        pass
    return found


def forget_roots(roots, *, store="ROOT"):
    """Ask Windows to delete each of these roots: one Windows question for each. Never raises.

    Returns `none` when there is nothing to delete. Otherwise `declined` or
    `blocked` when Windows kept at least one root, else `removed`, else
    `absent`. Every root is asked about, also after a No: each answer is the
    person's own, and a root that stays has no signing key anywhere.
    """
    outcomes = set()
    for der in roots or ():
        try:
            outcomes.add(remove_root(der, store=store))
        except (OSError, ValueError, RuntimeError):
            outcomes.add("blocked")
    return next((outcome for outcome in ("declined", "blocked", "removed", "absent") if outcome in outcomes), "none")
