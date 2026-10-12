import {devNull} from 'node:os';

/** Trusted launcher environment only; personal configuration stays unchanged. */
export function repositoryEnvironment(environment) {
 const result={...environment};
 if(result.AMPLIFIER_GITHUB_ACCESS!=='anonymous')return result;
 for(const key of Object.keys(result))if(key.startsWith('GIT_CONFIG_')||['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN','GIT_ASKPASS','SSH_ASKPASS'].includes(key))delete result[key];
 Object.assign(result,{GIT_TERMINAL_PROMPT:'0',GIT_CONFIG_GLOBAL:devNull,GIT_CONFIG_SYSTEM:devNull});
 const settings=[['credential.helper',''],['http.extraHeader',''],['http.cookieFile',''],['core.askPass','']];
 result.GIT_CONFIG_COUNT=String(settings.length);
 settings.forEach(([key,value],index)=>{result[`GIT_CONFIG_KEY_${index}`]=key;result[`GIT_CONFIG_VALUE_${index}`]=value;});
 return result;
}
