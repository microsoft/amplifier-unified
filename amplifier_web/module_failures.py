"""Allowlisted module-load diagnostics shared by worker, service and UI state."""
import json
from pathlib import Path
import re

from .deployment import write_private

REMEDIATION = {
    'invalid_package_layout': 'Check the module package layout and Python package files.',
    'missing_source': 'Check that the configured module source exists and is available.',
    'invalid_entry_point': 'Check the module entry point and async mount function.',
    'invalid_module_metadata': 'Check the declared module type and metadata.',
    'validation_failed': 'Check the module contract and its required dependencies.',
    'unknown': 'The module could not be loaded. Check its configuration and dependencies.',
}


def safe_failures(values):
    result=[]
    for row in values[:100] if isinstance(values,list) else []:
        if not isinstance(row,dict):continue
        module=row.get('module',row.get('module_id'))
        if not isinstance(module,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',module):module='unknown'
        kind=row.get('type',row.get('module_type'))
        if kind not in ('tool','hook','provider','orchestrator','context','resolver'):kind='unknown'
        reason=row.get('reason_code')
        if not isinstance(reason,str) or reason not in REMEDIATION:reason='unknown'
        result.append({'module':module,'type':kind,'reason_code':reason,'guidance':REMEDIATION[reason]})
    return result


class ConfiguredModuleError(RuntimeError):
    def __init__(self,failures):
        self.failures=safe_failures(failures)
        super().__init__('Configured modules failed to mount: '+ '; '.join(
            row['module']+': '+row['guidance'] for row in self.failures))


def persist_failures(directory,failures):
    error=ConfiguredModuleError(failures)
    write_private(Path(directory)/'module-load-failures.json',json.dumps(error.failures,indent=2))
    return error
