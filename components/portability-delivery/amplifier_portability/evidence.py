"""Bounded byte-preserving owner evidence; imported observations grant no execution."""
import hashlib
import json
import re

MAX_OWNER_BYTES=8*1024*1024

def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def _validate_owner(owner,revision,limit_bytes):
    if not isinstance(owner,str) or not re.fullmatch(r'[a-z][a-z0-9.-]{0,99}',owner):raise ValueError('Stable public evidence owner name required')
    if not isinstance(revision,(str,int)) or isinstance(revision,bool):raise ValueError('Opaque evidence revision required')
    if type(limit_bytes) is not int or not 1<=limit_bytes<=MAX_OWNER_BYTES:raise ValueError('Owner evidence exceeds configured bound')

def _decode(text,limit):
    from .capsule import validate_public
    if not isinstance(text,str) or len(text.encode('utf-8'))>limit:raise ValueError('Owner evidence exceeds bound; require explicit reviewed omissions')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate key in owner evidence')
            result[key]=value
        return result
    value=json.loads(text,object_pairs_hook=unique)
    validate_public(value)
    return value

def envelope(owner,revision,body,*,omissions=None,limit_bytes=MAX_OWNER_BYTES):
    _validate_owner(owner,revision,limit_bytes)
    raw=canonical_bytes(body);text=raw.decode('utf-8');_decode(text,limit_bytes)
    missing=omissions or []
    if not isinstance(missing,list) or len(canonical_bytes(missing))>65536:raise ValueError('Bounded explicit evidence omissions required')
    from .capsule import validate_public
    validate_public(missing)
    return {'owner':owner,'version':1,'revision':revision,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'encoding':'json-utf8','bodyJson':text,'omissions':missing,'disposition':'partial' if missing else 'complete','executionAuthority':False}

def verify(value,*,expected_owner=None,limit_bytes=MAX_OWNER_BYTES,accept_partial=False):
    if not isinstance(value,dict) or set(value)!={'owner','version','revision','sha256','bytes','encoding','bodyJson','omissions','disposition','executionAuthority'}:raise ValueError('Exact owner evidence envelope required')
    if type(value['version']) is not int or value['version']!=1 or value['executionAuthority'] is not False or value['encoding']!='json-utf8':raise ValueError('Evidence never transfers execution authority')
    _validate_owner(value['owner'],value['revision'],limit_bytes)
    _decode(value['bodyJson'],limit_bytes);raw=value['bodyJson'].encode('utf-8')
    if type(value['bytes']) is not int or value['bytes']!=len(raw) or value['sha256']!=hashlib.sha256(raw).hexdigest() or expected_owner and value['owner']!=expected_owner:raise ValueError('Owner evidence integrity or identity mismatch')
    omissions=value['omissions']
    if not isinstance(omissions,list) or len(canonical_bytes(omissions))>65536 or value['disposition']!=('partial' if omissions else 'complete'):raise ValueError('Invalid evidence omission disposition')
    from .capsule import validate_public
    validate_public(omissions)
    if omissions and not accept_partial:raise ValueError('Partial evidence needs explicit preview acceptance')
    return value

def decode_body(value,**options):
    verify(value,**options)
    return _decode(value['bodyJson'],options.get('limit_bytes',MAX_OWNER_BYTES))
