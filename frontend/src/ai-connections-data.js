export const aiServices=[
 {module:'provider-openai-chatgpt',name:'ChatGPT',description:'Sign in with your ChatGPT account.',auth:'signin'},
 {module:'provider-openai',name:'OpenAI API',description:'Use an API key. API billing is separate from ChatGPT.',auth:'key'},
 {module:'provider-anthropic',name:'Anthropic',description:'Connect with your Anthropic API key.',auth:'key'},
 {module:'provider-gemini',name:'Google Gemini',description:'Connect with your Gemini API key.',auth:'key'},
 {module:'provider-github-copilot',name:'GitHub Copilot',description:'Connect with a supported GitHub token.',auth:'token'},
];
export function aiService(module){return aiServices.find(s=>s.module===module)||{module,name:module?.replace(/^provider-/,'')||'AI service',auth:'key'};}
export function setupOperation(state,action,id){return state.setup?.operations?.[action+':'+id];}
export function setupPending(operation){return ['working','pending','queued'].includes(operation?.phase);}
export function matchingSetupOperation(state,pending){if(!pending)return null;const op=setupOperation(state,pending.action,pending.id);return op?.commandId===pending.commandId?op:null;}
