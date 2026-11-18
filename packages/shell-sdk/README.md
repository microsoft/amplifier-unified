# Amplifier shell SDK

`@amplifier/shell-sdk` contains dependency-free helpers and TypeScript declarations
for independently authored navigation, Canvas renderers and shell components.
Package version `1.0.0` describes the existing host interface `apiVersion: "1.0"`.
It does not version the application or declare compatibility with future APIs.

This repository owns the SDK and authoring example extracted from Unified; see
[the exact source and history](ORIGIN.json). Host contracts remain with Unified.
Initial distribution uses qualified GitHub release tarballs; npm registry
publication is separate.

Run `npm test` from this repository to produce and qualify
`dist/amplifier-shell-sdk-1.0.0.tgz`. The check installs that tarball outside the
checkout, compiles a strict TypeScript consumer and builds the example shipped
inside it. Temporary dependencies are removed automatically. Node.js 22 and npm are the contributor baseline.

Install a qualified tarball in a component project:

```sh
npm install /path/to/amplifier-shell-sdk-1.0.0.tgz
```

The package includes `examples/shell-controls`. Copy that directory to begin a
separate component, then follow its README. The SDK does not require a Unified
checkout or another React installation. The package's public host API remains
`1.0`; its own package releases can advance independently.

Import the package by name and accept React from the host:

```js
import {defineModule, useShellComponent} from '@amplifier/shell-sdk';

export default defineModule(({React}) => function Component({host}) {
  const snapshot = useShellComponent(React, host);
  return React.createElement('span', null, snapshot.slot);
});
```

Bundle the SDK into a single browser ESM artifact. Do not bundle another React,
import private Unified modules, or substitute a local conversation store for
the host snapshot. `useNavigation` and `useCanvas` subscribe to their respective
host interfaces in the same way. The included declarations describe each host.

The host owns manifest compatibility, capability discovery, validation and
activation. A successful build is not acceptance: discover the installed schema,
stage the manifest and artifact, validate, preview/apply and verify in the target
client. Native extensions run as trusted same-origin code; this SDK is not an
isolation boundary. Keep dirty-edit, generation, recovery and action-receipt
semantics intact.

See the [component contract](https://github.com/microsoft/amplifier-unified/blob/main/docs/shell/components.md),
[navigation and renderer contracts](https://github.com/microsoft/amplifier-unified/blob/main/docs/shell/README.md),
and [standalone controls example](examples/shell-controls/README.md).
Those host contracts remain canonical; the SDK does not duplicate their validator.

The runtime stays browser JavaScript; declarations give authors compiler feedback.
React comes from the host, while esbuild and TypeScript are authoring/check tools.
Neither those tools nor a native runtime is required by an installed component.

## Release and distribution

`npm test` emits the package, a checksum, and its build-input receipt. A tag named
`v<package version>` runs the same checks on Linux, macOS and Windows; only after
those checks pass does the release job publish the Linux-produced tarball to
GitHub. Keep `RELEASE_NOTES.md` current before tagging. npm registry publication
is separate until namespace ownership is established.

Unified should consume an exact released tarball and checksum, then qualify its
host contract before updating its packaged default. The SDK may release without
changing that default. In-repository source copies in Unified become generated
artifact contents at cutover, with this repository as the sole editing owner.
A successful package build does not establish host acceptance.

## Repository stewardship

The public source home is [bkrabach/amplifier-shell-sdk](https://github.com/bkrabach/amplifier-shell-sdk).
It preserves the original three-commit SDK history through
[`1909df59`](https://github.com/bkrabach/amplifier-shell-sdk/commit/1909df59cd9607a33a5f680e90352b628095dfbe),
previously hosted in the now-deleted Microsoft repository. [ORIGIN.json](ORIGIN.json)
continues to identify the original Unified extraction; its historical hashes are unchanged.
The package name, version, runtime, declarations, tests and example are unchanged
by this repository move. A new home does not publish a package or change any
Unified client selection; new release artifacts must be qualified separately.

This repository is hosted in a personal account. It carries the Microsoft OSS
boilerplate required by the
[repository audit recipe](https://github.com/microsoft/amplifier/blob/e8eb5540c70ecd970807742b3b0900b52f0b770e/recipes/repo-audit.yaml),
using [amplifier-core at 9614052](https://github.com/microsoft/amplifier-core/tree/9614052f7c9e493977dcba3f5c98ebd4d8591ec5)
as that recipe's reference. Microsoft organization registration, maintainer teams,
CLA automation and support coverage are not established by copying these files.
See [SECURITY.md](SECURITY.md), [SUPPORT.md](SUPPORT.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Contributing

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
