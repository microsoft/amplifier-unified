import {Schema,Slice} from 'prosemirror-model';
import {EditorState,TextSelection} from 'prosemirror-state';
import {schema as markdownSchema,MarkdownParser,defaultMarkdownParser,defaultMarkdownSerializer} from 'prosemirror-markdown';
import {InputRule,inputRules,wrappingInputRule,textblockTypeInputRule,undoInputRule} from 'prosemirror-inputrules';
import {keymap} from 'prosemirror-keymap';
import {baseKeymap,chainCommands,newlineInCode,createParagraphNear,liftEmptyBlock,splitBlock,toggleMark,setBlockType} from 'prosemirror-commands';
import {history,undo,redo} from 'prosemirror-history';
import {splitListItem,liftListItem,sinkListItem,wrapInList} from 'prosemirror-schema-list';

// Markdown image references remain editable without fetching remote images.
// Actual images use the existing attachment upload path.
export const composerSchema=new Schema({nodes:markdownSchema.spec.nodes.update('image',{
 ...markdownSchema.spec.nodes.get('image'),
 toDOM:node=>['span',{'class':'a-composer-image-reference',title:node.attrs.src},node.attrs.alt||'Image reference'],
}),marks:markdownSchema.spec.marks});
// Give the composer its own tokenizer; raw web links and labeled Markdown
// links share the parser's URL validation without changing other consumers.
const tokenizer=new defaultMarkdownParser.tokenizer.constructor('commonmark',{html:false,linkify:true}).enable('linkify');
export const composerParser=new MarkdownParser(composerSchema,tokenizer,defaultMarkdownParser.tokens);
export const composerMarkdown=doc=>defaultMarkdownSerializer.serialize(doc);
export const parseComposer=text=>composerParser.parse(text||'');
export function composerClipboard(slice){
 const content=slice.content.firstChild?.isInline?composerSchema.nodes.paragraph.create(null,slice.content):slice.content;
 return composerMarkdown(composerSchema.nodes.doc.create(null,content));
}

function markRule(pattern,mark){
 return new InputRule(pattern,(state,match,start,end)=>{
  const from=start+match[0].indexOf(match[1]),text=match[2];
  const marks=mark.addToSet(state.doc.resolve(from).marks());
  const tr=state.tr.replaceWith(from,end,composerSchema.text(text,marks));
  return tr.setSelection(TextSelection.create(tr.doc,from+text.length)).removeStoredMark(mark);
 },{inCodeMark:false});
}
export function composerRules(){
 const {nodes,marks}=composerSchema;
 return inputRules({rules:[
  markRule(/(?:^|[\s(])(\*\*([^*\s](?:[^*]*[^*\s])?)\*\*)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(__([^_\s](?:[^_]*[^_\s])?)__)$/,marks.strong.create()),
  markRule(/(?:^|[\s(])(\*([^*\s](?:[^*]*[^*\s])?)\*)$/,marks.em.create()),
  markRule(/(?:^|[\s(])(_([^_\s](?:[^_]*[^_\s])?)_)$/,marks.em.create()),
  markRule(/(`([^`]+)`)$/,marks.code.create()),
  new InputRule(/\[([^\]\n]+)\]\(([^\s)]+)\)$/,(state,match,start,end)=>{
   const parsed=parseComposer(match[0]).firstChild;
   if(!parsed?.firstChild?.marks.some(mark=>mark.type===marks.link))return null;
   const tr=state.tr.replaceWith(start,end,parsed.content);
   return tr.setSelection(TextSelection.create(tr.doc,start+parsed.content.size)).removeStoredMark(marks.link);
  },{inCodeMark:false}),
  new InputRule(/(?:^|\s)(https?:\/\/\S+)\s$/,(state,match,start,end)=>{
   const parsed=parseComposer(match[1]).firstChild;
   if(!parsed?.firstChild?.marks.some(mark=>mark.type===marks.link))return null;
   const from=start+match[0].indexOf(match[1]);
   return state.tr.replaceWith(from,end,parsed.content).insertText(' ',from+parsed.content.size).removeStoredMark(marks.link);
  },{inCodeMark:false}),
  textblockTypeInputRule(/^(#{1,6})\s$/,nodes.heading,match=>({level:match[1].length})),
  wrappingInputRule(/^\s*([-+*])\s$/,nodes.bullet_list,{tight:true}),
  wrappingInputRule(/^(\d+)\.\s$/,nodes.ordered_list,match=>({order:+match[1],tight:true}),(match,node)=>node.childCount+node.attrs.order===+match[1]),
  wrappingInputRule(/^>\s$/,nodes.blockquote),
  textblockTypeInputRule(/^```([\w-]*)\s$/,nodes.code_block,match=>({params:match[1]})),
 ]});
}

export function composerFormat(state){
 const {$from,from,to}=state.selection;
 let block='text';
 for(let depth=$from.depth;depth>0;depth--){
  const node=$from.node(depth);
  if(node.type.name==='bullet_list'||node.type.name==='ordered_list'){block=node.type.name;break;}
  if(node.type.name==='heading')block='h'+node.attrs.level;
 }
 const active=name=>state.doc.rangeHasMark(from,to,composerSchema.marks[name]);
 return {block,bold:active('strong'),italic:active('em'),link:active('link')};
}

export function formatComposerBlock(view,kind){
 const {nodes}=composerSchema;
 if(!['text','h1','h2','h3','bullet_list','ordered_list'].includes(kind))return;
 if(!(view.state.selection instanceof TextSelection))view.dispatch(view.state.tr.setSelection(TextSelection.between(view.state.selection.$from,view.state.selection.$to)));
 // Lift just the selected items before converting, leaving their siblings intact.
 while(liftListItem(nodes.list_item)(view.state,view.dispatch)){}
 setBlockType(kind.startsWith('h')?nodes.heading:nodes.paragraph,kind.startsWith('h')?{level:Number(kind[1])}:null)(view.state,view.dispatch);
 if(kind.endsWith('_list'))wrapInList(nodes[kind],{tight:true})(view.state,view.dispatch);
 view.focus();
}

export function setComposerLink(view,value){
 const href=value.trim(),{from,to}=view.state.selection;
 if(from===to)return false;
 if(href&&(!/^(https?:\/\/|mailto:|\/(?!\/)|#)/i.test(href)||/[\s\u0000-\u001f\u007f]/.test(href)||!tokenizer.validateLink(href)))return false;
 const tr=view.state.tr.removeMark(from,to,composerSchema.marks.link);
 if(href)tr.addMark(from,to,composerSchema.marks.link.create({href}));
 view.dispatch(tr);view.focus();return true;
}
export const composerNewline=chainCommands(newlineInCode,splitListItem(composerSchema.nodes.list_item),createParagraphNear,liftEmptyBlock,splitBlock);
export function composerState(text){
 const {marks,nodes}=composerSchema;
 return EditorState.create({schema:composerSchema,doc:parseComposer(text),plugins:[
  composerRules(),history(),keymap({
   'Mod-z':undo,'Shift-Mod-z':redo,'Mod-y':redo,
   'Mod-b':toggleMark(marks.strong),'Mod-i':toggleMark(marks.em),'Mod-`':toggleMark(marks.code),
   'Backspace':chainCommands(undoInputRule,(state,dispatch)=>state.selection.empty&&state.selection.$from.parentOffset===0&&liftListItem(nodes.list_item)(state,dispatch)),
   'Tab':sinkListItem(nodes.list_item),'Shift-Tab':liftListItem(nodes.list_item),
  }),keymap(baseKeymap),
 ]});
}
export function pasteMarkdown(view,text){
 if(view.state.selection.$from.parent.type.spec.code){view.dispatch(view.state.tr.insertText(text).scrollIntoView());return;}
 const doc=parseComposer(text);
 view.dispatch(view.state.tr.replaceSelection(Slice.maxOpen(doc.content)).scrollIntoView());
}
