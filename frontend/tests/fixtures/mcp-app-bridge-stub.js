export class AppBridge{
 constructor(...args){this.args=args;this.contexts=[];globalThis.mcpTestBridges.push(this)}
 async connect(){}
 setHostContext(value){this.contexts.push(value)}
 async close(){this.closed=true}
 async sendToolInput(){}
 async sendToolResult(){}
}
export class PostMessageTransport{}
