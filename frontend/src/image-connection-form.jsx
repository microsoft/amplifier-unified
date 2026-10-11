import React from 'react';
import {RefreshCw} from 'lucide-react';
import {SettingsActions} from './settings-layout';
import {WorkingLabel} from './working-label';

export function ImageConnectionForm({catalog,model,enabled,busy,saving,loading,module,hasConfiguration,hideHeading,hideRefresh,onChange,onRefresh,onSave,onConnections}){
 const supported=catalog?.supported===true,models=catalog?.models||[];
 const automatic=catalog?.metadata?.imageGeneration?.automaticModel;
 const choice=model||automatic||'';
 const unavailable=catalog?.supported===false;
 return <>
  {!hideHeading&&<><h4>Image generation</h4><p>Create and edit images using this account. Your chat model stays the same.</p></>}
  {unavailable?<>
   <p role="status">{module==='provider-gemini'?'Google Gemini can generate images, but this installed connection does not yet expose image generation here.':module==='provider-anthropic'?'This connection works for chat and image understanding, but does not generate images.':'Image generation is not available through this installed connection.'}</p>
   <p>You can use another connection for images while keeping this one for chat.</p>
   <button type="button" className="a-soft" disabled={busy} onClick={onConnections}>Choose another connection</button>
  </>:!supported?<p role="status">{loading?'Checking image support…':'Check this connection for image support.'}</p>:null}
  <fieldset className="a-image-connection-fields" disabled={busy}>
   {(supported||hasConfiguration)&&<label><input type="checkbox" checked={enabled} onChange={e=>onChange({imagesEnabled:e.target.checked})}/>Enable image generation</label>}
   {supported&&enabled&&<>
    <label htmlFor="ai-image-model">Image model</label><select id="ai-image-model" value={choice} onChange={e=>onChange({imageModel:e.target.value})}>
     {automatic?<option value={automatic}>Automatic · latest supported</option>:<option value="">Choose an image model</option>}
     {model&&model!==automatic&&!models.some(row=>row.id===model)&&<option value={model} disabled>{model} (unavailable saved choice)</option>}
     {models.map(row=><option key={row.id} value={row.id}>{row.display_name||row.id}</option>)}
    </select>
    {automatic&&choice===automatic&&<p className="a-caption">Uses the latest stable image model available through this account when you request an image. Choose a specific model to keep it fixed.</p>}
    {!models.length&&<p role="status">No compatible image models were returned. Check this account’s access, then refresh.</p>}
    <p className="a-caption">Image requests use this account and may have separate usage charges.</p>
   </>}
   {!hideRefresh&&<button type="button" className="a-link" aria-busy={loading||undefined} onClick={onRefresh}><RefreshCw/><WorkingLabel active={loading} working="Checking…">{unavailable?'Check again':'Refresh image models'}</WorkingLabel></button>}
  </fieldset>
  {(supported||hasConfiguration)&&<SettingsActions><button type="button" className="a-primary" aria-busy={saving||undefined} data-action="providers.configureImages" disabled={busy||(enabled&&(!supported||!models.length||!(choice===automatic||models.some(row=>row.id===choice))))} onClick={onSave}><WorkingLabel active={saving} working="Saving…">Save image settings</WorkingLabel></button></SettingsActions>}
  {supported&&<p className="a-caption">Saved for new work. A running request finishes with its current settings.</p>}
 </>;
}
