import React, {useEffect, useRef, useState} from 'react';
import {ArrowLeft, ArrowRight, Box, Cable, Check, ChevronRight, Code2, Download, ExternalLink, Globe, Play, Plus, RefreshCw, Settings2, Trash2, Unplug} from 'lucide-react';
import {useListFilter} from './list-filter.jsx';
import {PathField, ResultNotice} from './settings-ui';
import './smart-tools.css';

const defaults = {page:'home', serverId:'', toolName:'', repository:'', ref:'', path:'', extras:'', connection:{id:'', name:'', command:'', args:'', env:'', cwd:''}, values:{}, raw:false, json:'{}'};
const working = operation => ['running','working','queued','pending','connecting','installing'].includes(operation?.status || operation?.phase);
const failed = operation => ['failed','error','interrupted'].includes(operation?.status || operation?.phase) || !!operation?.error || operation?.result?.isError === true;
const pretty = value => JSON.stringify(value, null, 2);
const list = value => Array.isArray(value) ? value : Object.values(value || {});
const title = name => name.replace(/[_.-]+/g, ' ').replace(/^./, character => character.toUpperCase());
const sourceOf = row => row?.repository || row?.repo || row?.url || '';
const phaseOf = operation => working(operation) ? 'working' : failed(operation) ? 'error' : 'ready';
const actionMessages = {'smartTools.catalog':'Catalog refreshed', 'smartTools.inspect':'Source inspected', 'smartTools.install':'Installation complete', 'smartTools.configure':'Connection saved', 'smartTools.connect':'Connected', 'smartTools.disconnect':'Disconnected', 'smartTools.remove':'Connection removed', 'smartTools.call':'Tool finished', 'smartTools.open':'Opened in canvas'};

function useEditor(state, act) {
  const shared = state.view?.smartToolsEditor;
  const [editor, setEditor] = useState({...defaults, ...shared});
  const current = useRef(editor);
  useEffect(() => {if (shared) {current.current = {...defaults, ...shared}; setEditor(current.current);}}, [shared]);
  const edit = patch => {const next = {...current.current, ...patch}; current.current = next; setEditor(next); act('view.update', {patch:{smartToolsEditor:next}});};
  return [editor, edit];
}

function operationFor(operations, action, target={}) {
  return [...operations].reverse().find(operation => operation.action === action && Object.entries(target).every(([key,value]) => {
    const found = operation.target?.[key] ?? operation.args?.[key] ?? (key === 'id' ? operation.serverId : key === 'name' ? operation.tool || operation.name : operation[key]);
    if (operation.target || operation.args) return (found ?? '') === (value ?? '');
    return found == null || found === value;
  }));
}

function OperationNotice({operation, busyMessage}) {
  if (!operation) return null;
  const message = operation.error || (operation.result?.isError ? 'The tool reported an error. See its result below.' : working(operation) ? busyMessage || 'Working…' : actionMessages[operation.action] || 'Finished');
  return <ResultNotice phase={phaseOf(operation)} message={typeof message === 'string' ? message : pretty(message)} detail={operation.status === 'interrupted' ? 'The previous app run ended before this request finished. Review the tool state before retrying.' : undefined}/>;
}

function ToolResult({operation}) {
  if (!operation || working(operation) || operation.result == null) return null;
  const result = operation.result;
  const stored = !!(result && typeof result === 'object' && result.$resource);
  const preview = stored ? result.summary ?? result.preview ?? null : result;
  const output = typeof preview === 'string' ? preview : preview == null ? '' : pretty(preview);
  return <div className="a-smart-result"><h4>Result</h4>{output && <pre tabIndex={0}>{output.slice(0,24000)}</pre>}{stored ? <p className="a-caption">The full result is saved{typeof result.bytes === 'number' ? ` (${Math.ceil(result.bytes / 1024).toLocaleString()} KB)` : ''}. Ask your agent to inspect it or show the details you need.</p> : output.length > 24000 && <p className="a-caption">Showing the first 24,000 characters. The full result is saved and available for your agent to inspect.</p>}</div>;
}

function parseEnvironment(text) {
  const entries = text.split('\n').map(line => line.trim()).filter(Boolean);
  const result = {};
  for (const entry of entries) {
    const match = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)$/.exec(entry);
    if (!match) throw new Error('Use CHILD_VARIABLE=APP_VARIABLE on each line. Enter variable names, not secret values.');
    if (Object.hasOwn(result, match[1])) throw new Error(`The variable ${match[1]} appears more than once.`);
    result[match[1]] = match[2];
  }
  return result;
}

function collectArguments(schema, values, raw, json) {
  if (raw || !schema?.properties || schema.oneOf || schema.anyOf || schema.$ref) {
    const parsed = JSON.parse(json);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Tool arguments must be a JSON object.');
    return parsed;
  }
  const result = {};
  for (const [name, field] of Object.entries(schema.properties)) {
    let value = values[name] ?? (field.default == null ? '' : field.enum || typeof field.default === 'object' ? pretty(field.default) : String(field.default));
    if (value === '') {if (schema.required?.includes(name)) throw new Error(`${field.title || title(name)} is required.`); continue;}
    if (field.enum) value = JSON.parse(value);
    else if (field.type === 'boolean') value = value === 'true';
    else if (field.type === 'integer' || field.type === 'number') {
      value = Number(value);
      if (!Number.isFinite(value) || field.type === 'integer' && !Number.isInteger(value)) throw new Error(`${field.title || title(name)} must be ${field.type === 'integer' ? 'a whole number' : 'a number'}.`);
    } else if (field.type === 'object' || field.type === 'array' || field.oneOf || field.anyOf || field.$ref || Array.isArray(field.type)) value = JSON.parse(value);
    result[name] = value;
  }
  return result;
}

function SchemaFields({schema, values, edit}) {
  return Object.entries(schema.properties || {}).map(([name, field]) => {
    const id = `smart-tool-field-${name}`, label = field.title || title(name), required = schema.required?.includes(name);
    const complex = field.type === 'object' || field.type === 'array' || field.oneOf || field.anyOf || field.$ref || Array.isArray(field.type);
    const defaultValue = field.default == null ? '' : field.enum || complex ? pretty(field.default) : String(field.default);
    const value = values[name] ?? defaultValue;
    const common = {id, value, 'data-action':'view.update', onChange:event => edit({...values, [name]:event.target.value}), 'aria-describedby':field.description ? id+'-hint' : undefined};
    return <div className="a-smart-field" key={name}><label htmlFor={id}>{label}{required && <span className="a-smart-required">Required</span>}</label>{field.enum ? <select {...common}><option value="">{required ? 'Choose…' : 'Tool default'}</option>{field.enum.map((choice,index) => <option key={index} value={JSON.stringify(choice)}>{typeof choice === 'string' ? choice : JSON.stringify(choice)}</option>)}</select> : field.type === 'boolean' ? <select {...common}><option value="">{required ? 'Choose…' : 'Tool default'}</option><option value="true">Yes</option><option value="false">No</option></select> : complex ? <textarea {...common} className="a-json-editor" spellCheck={false} placeholder={field.type === 'array' ? '[]' : '{}'}/> : field.type === 'number' || field.type === 'integer' ? <input {...common} type="number" step={field.type === 'integer' ? '1' : 'any'} min={field.minimum} max={field.maximum}/> : <textarea {...common} rows={2} placeholder={field.examples?.[0] == null ? '' : String(field.examples[0])}/>}{field.description && <p id={id+'-hint'} className="a-caption">{field.description}</p>}</div>;
  });
}

export function SmartToolsSettings({state, act}) {
  const [editor, edit] = useEditor(state, act);
  const [local, setLocal] = useState(null), [error, setError] = useState(''), [opening, setOpening] = useState(null);
  const smart = state.smartTools || {}, servers = list(smart.servers), catalog = list(smart.catalog), installations = list(smart.installations), operations = list(smart.operations);
  const server = servers.find(item => item.id === editor.serverId);
  const tools = list(server?.tools).filter(item => (item._meta?.ui?.visibility || ['model','app']).includes('model')), tool = tools.find(item => item.name === editor.toolName);
  const operation = (action, target={}) => local?.action === action ? local : operationFor(operations, action, target);
  const run = async (action, args={}) => {
    setError(''); setLocal({action, status:'pending', target:args});
    try {const receipt = await act(action, args); if (!receipt) throw new Error('The request could not be sent. Please try again.'); setLocal(null); return receipt;}
    catch (caught) {setLocal({action, status:'failed', error:caught.message, target:args}); return null;}
  };
  const navigate = (page, patch={}) => {setError('');setLocal(null);edit({page,...patch});};
  const connection = {...defaults.connection, ...editor.connection};
  const editConnection = patch => edit({connection:{...connection,...patch}});
  const activeOperation = [...operations].reverse().find(working);
  const anyPending = working(local) || operations.some(item => working(item) && (
    editor.page === 'source' ? item.target?.repository === editor.repository.trim() :
    editor.page === 'connection' ? item.action === 'smartTools.configure' && (item.target?.id || '') === connection.id :
    ['server','tool'].includes(editor.page) ? item.target?.id === server?.id : false
  ));
  const [shownServers, serverFilter] = useListFilter(state, act, 'smart-tool-connections', servers, row => [row.name,row.command,row.status], 'Filter connections');
  const [shownCatalog, catalogFilter] = useListFilter(state, act, 'smart-tool-catalog', catalog, row => [row.name,row.id,row.description,sourceOf(row)], 'Filter catalog');
  const [shownTools, toolFilter] = useListFilter(state, act, 'smart-tool-actions-'+(server?.id || ''), tools, row => [row.name,row.title,row.description], 'Filter tools');
  const back = editor.page === 'tool' ? 'server' : 'home';
  const openConnection = item => navigate('connection', {connection:item ? {id:item.id,name:item.name,command:item.command,args:(item.args||[]).join('\n'),env:Object.entries(item.env||{}).map(([key,value]) => key+'='+value).join('\n'),cwd:item.cwd||''} : defaults.connection});
  const sourceArgs = () => ({repository:editor.repository.trim(), ...(editor.ref.trim() ? {ref:editor.ref.trim()} : {}), ...(editor.path.trim() ? {path:editor.path.trim()} : {})});
  const sourceTarget = {repository:editor.repository.trim(),ref:editor.ref.trim(),path:editor.path.trim()};
  const inspectionOp = operation('smartTools.inspect', sourceTarget);
  const installationOp = operation('smartTools.install', sourceTarget);
  const inspection = !working(inspectionOp) && !failed(inspectionOp) ? inspectionOp?.result : null;
  const installed = installations.find(item => sourceOf(item) === editor.repository.trim());
  const installation = !working(installationOp) && !failed(installationOp) ? installationOp?.result || installed : installed;
  const callOperation = operation('smartTools.call', {id:server?.id,name:tool?.name,sessionId:state.selectedSessionId});
  const latestViewCall = [...operations].reverse().find(item => item.action === 'smartTools.call' && item.status === 'completed' && item.target?.id === server?.id && item.target?.name === tool?.name && item.target?.sessionId === state.selectedSessionId);
  const openView = async () => {
    const sessionId = state.selectedSessionId;
    const receipt = await run('smartTools.open', {id:server.id,tool:tool.name,...(sessionId ? {sessionId} : {}),...(latestViewCall ? {operationId:latestViewCall.id} : {})});
    if (receipt?.operationId) setOpening({id:receipt.operationId,sessionId,serverId:server.id,toolName:tool.name});
  };
  useEffect(() => {
    if (!opening) return;
    const completed = operations.find(item => item.id === opening.id);
    if (!completed || working(completed)) return;
    if (completed.status === 'completed' && state.selectedSessionId === opening.sessionId && state.view?.panel === 'settings' && editor.page === 'tool' && editor.serverId === opening.serverId && editor.toolName === opening.toolName) act('view.update', {patch:{panel:null}});
    setOpening(null);
  }, [opening, operations, state.selectedSessionId, state.view?.panel, editor.page, editor.serverId, editor.toolName]);
  const toolSchema = tool?.inputSchema || {type:'object',properties:{}};
  const rawOnly = !toolSchema.properties || !!(toolSchema.oneOf || toolSchema.anyOf || toolSchema.$ref);
  const lastConnectionOperation = ['smartTools.configure','smartTools.connect','smartTools.disconnect','smartTools.remove'].map(action => operation(action, {id:server?.id})).filter(Boolean).sort((a,b) => (b.updatedAt || b.createdAt || Infinity) - (a.updatedAt || a.createdAt || Infinity))[0];

  return <section className="a-settings-section a-smart-tools" data-part="smart-tools-settings">
    {editor.page !== 'home' && <button type="button" className="a-link a-smart-back" data-action="view.update" onClick={() => navigate(back)}><ArrowLeft/>Back to {back === 'server' ? server?.name || 'connection' : 'Smart Tools'}</button>}
    {error && <ResultNotice phase="error" message={error}/>}

    {editor.page === 'home' && <>
      <p>Add specialist tools to your conversations. Tools with an interactive view can open alongside your chat in the canvas.</p>
      <div className="a-smart-entry-actions"><button type="button" className="a-soft" data-action="smartTools.catalog" onClick={() => {navigate('catalog');if(!catalog.length)run('smartTools.catalog');}}><Globe/>Browse catalog<ChevronRight/></button><button type="button" className="a-soft" data-action="view.update" onClick={() => navigate('source')}><Download/>Add from Git<ChevronRight/></button><button type="button" className="a-soft" data-action="view.update" onClick={() => openConnection()}><Plus/>Add MCP connection<ChevronRight/></button></div>
      <div className="a-smart-heading"><h4>Connections</h4><small>{servers.filter(item => item.status === 'connected' || item.status === 'ready').length} ready · {servers.length} configured</small></div>
      {servers.length > 4 && serverFilter}
      <div className="a-smart-list">{shownServers.map(item => <button type="button" className="a-smart-list-row" key={item.id} data-action="view.update" onClick={() => navigate('server',{serverId:item.id})}><Cable/><span><strong>{item.name}</strong><small>{item.error ? 'Needs attention' : item.status || 'Not connected'}{item.tools?.length ? ` · ${item.tools.length} tools` : ''}{item.uiCapable || item.tools?.some(entry => entry._meta?.ui?.resourceUri) ? ' · Canvas views' : ''}</small></span><ChevronRight/></button>)}</div>
      {!servers.length && <p className="a-smart-empty">No connections yet. Install a tool from the catalog, or connect an existing MCP server.</p>}
      {!!installations.length && <><div className="a-smart-heading"><h4>Installed packages</h4><small>{installations.length}</small></div><div className="a-smart-list">{installations.map((item,index) => <button type="button" key={item.id || sourceOf(item) || index} className="a-smart-list-row" data-action="view.update" onClick={() => navigate('source',{repository:sourceOf(item),ref:item.ref||'',path:item.path||''})}><Box/><span><strong>{item.name || item.manifest?.name || sourceOf(item)}</strong><small>{item.status || 'Installed'} · {item.commit?.slice(0,8) || item.version || 'View package details'}</small></span><ChevronRight/></button>)}</div><p className="a-caption">Installing a package and connecting its MCP server are separate steps.</p></>}
      {activeOperation && <OperationNotice operation={activeOperation}/>}
      <OperationNotice operation={operation('smartTools.remove')}/>
    </>}

    {editor.page === 'catalog' && <>
      <div className="a-smart-heading"><h4>Smart Tools catalog</h4><button type="button" className="a-icon" aria-label="Refresh Smart Tools catalog" data-action="smartTools.catalog" disabled={working(operation('smartTools.catalog'))} onClick={() => run('smartTools.catalog')}><RefreshCw/></button></div>
      <p>Explore the community catalog, then inspect a source before installing it.</p>
      <OperationNotice operation={operation('smartTools.catalog')} busyMessage="Loading the catalog…"/>
      {catalogFilter}<div className="a-smart-list">{shownCatalog.map(item => <button type="button" key={item.id || sourceOf(item)} className="a-smart-list-row" data-action="view.update" onClick={() => {navigate('source',{repository:sourceOf(item),ref:item.ref||'',path:item.path||'',extras:''});run('smartTools.inspect',{repository:sourceOf(item),...(item.ref ? {ref:item.ref}:{}),...(item.path ? {path:item.path}: {})});}}><Box/><span><strong>{item.name || item.id}</strong><small>{item.description || sourceOf(item)}</small></span><ChevronRight/></button>)}</div>
      {!catalog.length && !working(operation('smartTools.catalog')) && <p className="a-smart-empty">The catalog has not loaded yet. Refresh to try again, or add a tool from a Git URL.</p>}
    </>}

    {editor.page === 'source' && <>
      <h4>Add from Git</h4><p>Inspect the tool’s manifest and source revision, then install the Python package into its own environment.</p>
      <label htmlFor="smart-tool-repository">Repository URL</label><input id="smart-tool-repository" type="url" value={editor.repository} placeholder="https://github.com/owner/tool" data-action="view.update" onChange={event => edit({repository:event.target.value})}/>
      <div className="a-form-grid"><div><label htmlFor="smart-tool-ref">Branch, tag or commit (optional)</label><input id="smart-tool-ref" value={editor.ref} placeholder="Repository default" data-action="view.update" onChange={event => edit({ref:event.target.value})}/></div><div><label htmlFor="smart-tool-path">Package folder (optional)</label><input id="smart-tool-path" value={editor.path} placeholder="Repository root" data-action="view.update" onChange={event => edit({path:event.target.value})}/></div></div>
      <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="smartTools.inspect" disabled={!editor.repository.trim() || anyPending} onClick={() => run('smartTools.inspect',sourceArgs())}><RefreshCw/>Inspect source</button></div><OperationNotice operation={inspectionOp} busyMessage="Reading the tool manifest…"/>
      {inspection && <div className="a-smart-source-summary"><strong>{inspection.name || inspection.manifest?.name || inspection.metadata?.name || 'Tool source'}</strong><p>{inspection.description || inspection.manifest?.description || inspection.metadata?.description}</p><dl><div><dt>Revision</dt><dd><code>{inspection.commit?.slice(0,12) || inspection.revision || editor.ref || 'Default branch'}</code></dd></div>{inspection.version || inspection.manifest?.version ? <div><dt>Version</dt><dd>{inspection.version || inspection.manifest?.version}</dd></div> : null}</dl>{(inspection.manifest?.requires || inspection.requires) && <div className="a-smart-requirements"><h5>Requirements from the tool author</h5><pre>{typeof (inspection.manifest?.requires || inspection.requires) === 'string' ? (inspection.manifest?.requires || inspection.requires) : pretty(inspection.manifest?.requires || inspection.requires)}</pre></div>}</div>}
      <label htmlFor="smart-tool-extras">Optional package extras</label><input id="smart-tool-extras" value={editor.extras} placeholder="For example, mcp" data-action="view.update" onChange={event => edit({extras:event.target.value})}/><p className="a-caption">Use extras documented by the tool author, separated by commas. An MCP adapter may be an optional extra.</p>
      <div className="a-dialog-actions"><button type="button" className="a-primary" data-action="smartTools.install" disabled={!editor.repository.trim() || anyPending} onClick={() => run('smartTools.install',{...sourceArgs(),...(editor.extras.trim() ? {extras:editor.extras.split(',').map(value => value.trim()).filter(Boolean)} : {})})}><Download/>{installed ? 'Install source revision' : 'Install package'}</button></div><OperationNotice operation={installationOp} busyMessage="Installing the package in its own environment…"/>
      {installation && <div className="a-smart-source-summary"><strong><Check/>Package installed</strong>{installation.binDir && <><p>Executables are available in:</p><code className="a-smart-path">{installation.binDir}</code></>}{installation.guidance && <p>{typeof installation.guidance === 'string' ? installation.guidance : pretty(installation.guidance)}</p>}<p>{installation.nextStep || 'Use the tool’s documented MCP executable and arguments to add a connection.'}</p><button type="button" className="a-soft" data-action="view.update" onClick={() => {openConnection();edit({connection:{...defaults.connection,name:installation.name || installation.manifest?.name || '',command:''}});}}><Cable/>Configure MCP connection<ArrowRight/></button></div>}
    </>}

    {editor.page === 'connection' && <form onSubmit={async event => {event.preventDefault();try {const env = parseEnvironment(connection.env);await run('smartTools.configure',{...(connection.id?{id:connection.id}:{}),name:connection.name.trim(),command:connection.command.trim(),args:connection.args.split('\n').map(value => value.trim()).filter(Boolean),env,...(connection.cwd.trim()?{cwd:connection.cwd.trim()}:{})});} catch(caught) {setError(caught.message);}}}>
      <h4>{connection.id ? 'Edit connection' : 'Add MCP connection'}</h4><p>Connect a local MCP server. Tools shared with agents are listed here; a server may reserve additional helpers for its interactive view.</p>
      <label htmlFor="smart-tool-connection-name">Display name</label><input id="smart-tool-connection-name" required value={connection.name} placeholder="My Smart Tool" data-action="view.update" onChange={event => editConnection({name:event.target.value})}/>
      <label htmlFor="smart-tool-command">Executable</label><input id="smart-tool-command" required value={connection.command} placeholder="/path/to/environment/bin/tool-mcp" spellCheck={false} data-action="view.update" onChange={event => editConnection({command:event.target.value})}/>
      <label htmlFor="smart-tool-arguments">Arguments — one per line</label><textarea id="smart-tool-arguments" value={connection.args} spellCheck={false} placeholder={'--storage\n/path/to/tool-data'} data-action="view.update" onChange={event => editConnection({args:event.target.value})}/><p className="a-caption">Each line is one argument. Shell quoting and shell commands are not evaluated.</p>
      <label htmlFor="smart-tool-cwd">Working folder (optional)</label><PathField id="smart-tool-cwd" value={connection.cwd} onChange={cwd => editConnection({cwd})} directory state={state} act={act} placeholder="Use the default working folder"/>
      <label htmlFor="smart-tool-environment">Environment variable references (optional)</label><textarea id="smart-tool-environment" value={connection.env} spellCheck={false} placeholder="OPENAI_API_KEY=MY_TOOL_API_KEY" data-action="view.update" onChange={event => editConnection({env:event.target.value})}/><p className="a-caption">Use CHILD_VARIABLE=APP_VARIABLE, one per line. Enter names of existing environment variables; no key values are stored here.</p>
      <OperationNotice operation={operation('smartTools.configure')} busyMessage="Saving the connection…"/>
      <div className="a-dialog-actions"><button className="a-primary" data-action="smartTools.configure" disabled={!connection.name.trim() || !connection.command.trim() || anyPending}><Check/>Save connection</button><button type="button" className="a-soft" data-action="view.update" onClick={() => navigate('home')}>View connections<ArrowRight/></button></div>
    </form>}

    {(editor.page === 'server' || editor.page === 'tool') && !server && <ResultNotice phase="neutral" message="This connection is no longer configured."/>}
    {editor.page === 'server' && server && <>
      <div className="a-smart-heading"><div><h4>{server.name}</h4><p>{server.status || 'Not connected'} · {tools.length} tools</p></div><button type="button" className="a-icon" aria-label={'Edit '+server.name} data-action="view.update" onClick={() => openConnection(server)}><Settings2/></button></div>
      {server.error && <ResultNotice phase="error" message={server.error}/>}
      <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="smartTools.connect" disabled={anyPending || ['connected','ready'].includes(server.status)} onClick={() => run('smartTools.connect',{id:server.id})}><Cable/>{server.status === 'connected' || server.status === 'ready' ? 'Connected' : 'Connect'}</button><button type="button" className="a-soft" data-action="smartTools.disconnect" disabled={anyPending || !['connected','ready'].includes(server.status)} onClick={() => run('smartTools.disconnect',{id:server.id})}><Unplug/>Disconnect</button></div>
      <OperationNotice operation={lastConnectionOperation} busyMessage="Updating the connection…"/>
      {tools.length > 4 && toolFilter}<div className="a-smart-list">{shownTools.map(item => <button type="button" key={item.name} className="a-smart-list-row" data-action="view.update" onClick={() => navigate('tool',{toolName:item.name,values:{},raw:false,json:'{}'})}><Code2/><span><strong>{item.title || item.annotations?.title || title(item.name)}</strong><small>{item.description || item.name}</small>{item._meta?.ui?.resourceUri && <small className="a-smart-view-label">Interactive canvas view</small>}</span><ChevronRight/></button>)}</div>
      {!tools.length && <p className="a-smart-empty">Connect to discover the tools supplied by this server.</p>}
      <div className="a-dialog-actions"><button type="button" className="a-link a-danger" disabled={anyPending} data-action="smartTools.remove" onClick={async () => {if(await run('smartTools.remove',{id:server.id}))navigate('home');}}><Trash2/>Remove connection</button></div><p className="a-caption">Removing a connection keeps the installed package and the tool’s saved work.</p>
    </>}

    {editor.page === 'tool' && server && !tool && <ResultNotice phase="neutral" message="This tool is no longer in the server’s tool list. Reconnect to discover its current tools."/>}
    {editor.page === 'tool' && tool && <>
      <h4>{tool.title || tool.annotations?.title || title(tool.name)}</h4><p>{tool.description}</p><code className="a-smart-tool-id">{server.name} / {tool.name}</code>
      {tool._meta?.ui?.resourceUri && <div className="a-dialog-actions"><button type="button" className="a-soft" data-action="smartTools.open" disabled={anyPending} onClick={openView}><ExternalLink/>Open interactive view</button></div>}
      <OperationNotice operation={operation('smartTools.open',{id:server.id,tool:tool.name})} busyMessage="Opening the canvas view…"/>
      <form onSubmit={event => {event.preventDefault();try {const args=collectArguments(toolSchema,editor.values,editor.raw,editor.json);run('smartTools.call',{id:server.id,name:tool.name,arguments:args});} catch(caught) {setError(caught.message);}}}>
        {!rawOnly && !editor.raw ? <SchemaFields schema={toolSchema} values={editor.values} edit={values => edit({values})}/> : <><label htmlFor="smart-tool-json">Tool arguments (JSON)</label><textarea id="smart-tool-json" className="a-json-editor" value={editor.json} spellCheck={false} data-action="view.update" onChange={event => edit({json:event.target.value})}/></>}
        {!rawOnly && <button type="button" className="a-link a-smart-advanced" data-action="view.update" aria-expanded={editor.raw} onClick={() => {try {if(editor.raw){const parsed=collectArguments(toolSchema,{},true,editor.json);if(Object.keys(parsed).some(key => !Object.hasOwn(toolSchema.properties,key)))throw new Error('These arguments include extra properties. Keep the JSON editor to preserve them.');const values=Object.fromEntries(Object.entries(parsed).map(([key,value]) => [key,toolSchema.properties?.[key]?.enum || typeof value === 'object' ? JSON.stringify(value) : String(value)]));edit({raw:false,values});}else {let value;try{value=collectArguments({...toolSchema,required:[]},editor.values,false,'{}');}catch {value={};}edit({raw:true,json:pretty(value)});}setError('');} catch(caught){setError(caught.message);}}}>{editor.raw ? 'Use form fields' : 'Edit arguments as JSON'}</button>}
        <div className="a-dialog-actions"><button className="a-primary" data-action="smartTools.call" disabled={anyPending || !['connected','ready'].includes(server.status)}><Play/>Run tool</button></div>
      </form><OperationNotice operation={callOperation} busyMessage="Running the tool…"/><ToolResult operation={callOperation}/>
    </>}
  </section>;
}
