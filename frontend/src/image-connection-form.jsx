import React from 'react';
import {RefreshCw} from 'lucide-react';
import {SettingsActions} from './settings-layout';
import {WorkingLabel} from './working-label';

export function ImageConnectionForm({catalog,model,enabled,busy,saving,loading,onChange,onRefresh,onSave}){
 const supported=catalog?.supported===true,models=catalog?.models||[];
 return <>
  <h4>Image generation</h4>
  <p>Use this connection to create and edit images in your conversations. Image requests may have separate usage charges.</p>
  <fieldset className="a-image-connection-fields" disabled={busy}>
   <label><input type="checkbox" checked={enabled} onChange={e=>onChange({imagesEnabled:e.target.checked})}/>Enable image generation</label>
   {enabled&&<>
    {supported?<><label htmlFor="ai-image-model">Image model</label><select id="ai-image-model" value={model} onChange={e=>onChange({imageModel:e.target.value})}>
     <option value="">Choose an image model</option>
     {model&&!models.some(row=>row.id===model)&&<option value={model}>{model} (saved choice)</option>}
     {models.map(row=><option key={row.id} value={row.id}>{row.display_name||row.id}</option>)}
    </select>{!models.length&&<p role="status">No compatible image models were returned. Check this account’s access, then refresh.</p>}</>:
     <p role="status">{catalog?'This connection does not offer image setup. Update its provider or choose another connection.':'Check this connection for image models.'}</p>}
    <button type="button" className="a-link" aria-busy={loading||undefined} data-action="providers.imageModels" onClick={onRefresh}><RefreshCw/><WorkingLabel active={loading} working="Checking…">Refresh image models</WorkingLabel></button>
    <p className="a-caption">Checking the catalog does not generate an image. Access is confirmed when you request your first image.</p>
   </>}
  </fieldset>
  <SettingsActions><button type="button" className="a-primary" aria-busy={saving||undefined} data-action="providers.configureImages" disabled={busy||(enabled&&(!supported||!models.some(row=>row.id===model)))} onClick={onSave}><WorkingLabel active={saving} working="Saving…">Save image settings</WorkingLabel></button></SettingsActions>
  <p className="a-caption">Applies to new conversations. Your chat model and existing tool restrictions are kept.</p>
 </>;
}
