import React,{createContext,useContext,useEffect,useState} from 'react';

// Namespaced host-context extension; other MCP Apps can ignore it.
export const MCP_VISIBILITY='com.microsoft.amplifier/visibility';
// A view's saved server binding owns availability, not the primary selection.
export function mcpConnectionKey(canvas,smartTools){
 const server=smartTools?.servers?.find(row=>row.id===canvas?.mcp?.serverId);
 return JSON.stringify([server?.status,server?.catalogState,server?.catalogRevision,server?.account,server?.accountBinding,canvas?.mcp?.catalogRevision]);
}
const VisibilityContext=createContext(true);
export function McpAppVisibilityProvider({visible,children}){
 return <VisibilityContext.Provider value={visible}>{children}</VisibilityContext.Provider>;
}
export function useMcpAppVisibility(open){
 const canvasVisible=useContext(VisibilityContext);
 const [documentVisible,setDocumentVisible]=useState(()=>document.visibilityState!=='hidden');
 useEffect(()=>{
  const change=()=>setDocumentVisible(document.visibilityState!=='hidden');
  document.addEventListener('visibilitychange',change);
  return()=>document.removeEventListener('visibilitychange',change);
 },[]);
 return open!==false&&canvasVisible&&documentVisible;
}
