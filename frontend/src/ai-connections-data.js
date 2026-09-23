import providerSources from '../../amplifier_web/provider_sources.json' with {type:'json'};
// Everyday choices; module IDs match amplifier-app-cli's canonical providers.
export const aiServices=[
 {module:'provider-github-copilot',name:'GitHub Copilot Subscription',description:'Use your GitHub Copilot subscription.',auth:'token'},
 {module:'provider-openai-chatgpt',name:'ChatGPT Subscription',description:'Sign in with your ChatGPT account.',auth:'signin'},
 {module:'provider-openai',name:'OpenAI API',description:'Use an API key. API billing is separate from ChatGPT.',auth:'key'},
 {module:'provider-anthropic',name:'Anthropic API',description:'Connect with your Anthropic API key.',auth:'key'},
 {module:'provider-gemini',name:'Google Gemini API',description:'Connect with your Gemini API key.',auth:'key'},
 {module:'provider-chat-completions',name:'OpenAI Compatible API',description:'Connect a local model or another compatible service.',auth:'optional-key'},
];
// Canonical known modules in microsoft/amplifier-app-cli/provider_sources.py.
const providerLabels=[...aiServices,
 {module:'provider-azure-openai',name:'Azure OpenAI'},
 {module:'provider-ollama',name:'Ollama'},
 {module:'provider-vllm',name:'vLLM'},
];
export const knownProviders=Object.keys(providerSources).map(module=>({...providerLabels.find(row=>row.module===module),module,source:providerSources[module]}));
export function aiService(module){return aiServices.find(s=>s.module===module)||{module,name:module?.replace(/^provider-/,'')||'AI service',auth:'key'};}
export function setupOperation(state,action,id){return state.setup?.operations?.[action+':'+id];}
export function setupPending(operation){return ['working','pending','queued'].includes(operation?.phase);}
export function matchingSetupOperation(state,pending){if(!pending)return null;const op=setupOperation(state,pending.action,pending.id);return op?.commandId===pending.commandId?op:null;}
