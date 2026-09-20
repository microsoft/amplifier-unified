import {defineModule,useNavigation} from '../../packages/shell-sdk/index.js';

// Authored outside the app: public SDK only, and the host supplies React.
export default defineModule(({React})=>function RecentNavigator({host}){
 const state=useNavigation(React,host),h=React.createElement;
 const [error,setError]=React.useState('');
 const run=(action,args)=>host.dispatch(action,args).catch(error=>setError(error.message));
 return h('section',{'aria-label':'Recent navigator',style:{padding:'10px 4px'}},
  h('h3',{style:{fontSize:14,margin:'0 0 12px'}},'Recent navigator'),
  h('label',{style:{display:'grid',gap:6,fontSize:12}},'Find a conversation',h('input',{type:'search',maxLength:500,'aria-label':'Navigator search',value:state.view.navFilter||'',style:{width:'100%',boxSizing:'border-box',padding:8,border:'1px solid var(--a-line)',borderRadius:8,background:'var(--a-surface)',color:'var(--a-ink)'},onChange:event=>run('view.update',{patch:{navFilter:event.target.value}})})),
  h('p',{style:{fontSize:11,color:'var(--a-muted)',margin:'10px 0'}},`${state.chatNavigation.total} matching conversations`),
  ...state.chatNavigation.items.slice(0,5).map(chat=>h('button',{key:chat.id,type:'button',onClick:()=>run('session.select',{id:chat.id}),style:{display:'block',width:'100%',textAlign:'left',padding:8,marginTop:4,border:'1px solid var(--a-line)',borderRadius:8,overflow:'hidden',textOverflow:'ellipsis',color:'var(--a-ink)'}},chat.title)),
  error&&h('p',{role:'alert'},error));
});
