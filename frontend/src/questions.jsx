import React,{useRef,useState} from 'react';
import './questions.css';

function QuestionCard({question,sessionId,dispatch}){
 const [option,setOption]=useState(''),[text,setText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef(false),request=useRef(null),q=question;
 async function submit(event){
  event.preventDefault();if(pending.current)return;
  const answer=option?{optionId:option}:{text:text.trim()};
  if(!option&&!answer.text)return;
  // Keep the same command after a lost reply. Closed revisions also prevent
  // duplicate delivery after reload or submission from a second device.
  const args={sessionId,id:q.id,expectedRevision:q.revision,...answer};
  const key=JSON.stringify(args);
  if(request.current?.key!==key)request.current={key,id:crypto.randomUUID()};
  pending.current=true;setBusy(true);setError('');
  try{await dispatch('question.answer',args,{id:request.current.id})}
  catch(error){setError(error.message||'The answer could not be confirmed. Your choice is kept.')}
  finally{pending.current=false;setBusy(false)}
 }
 async function skip(){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');
  try{await dispatch('question.cancel',{sessionId,id:q.id,expectedRevision:q.revision,reason:'Skipped by the user'})}
  catch(error){setError(error.message)}finally{pending.current=false;setBusy(false)}
 }
 const delivery=q.delivery?.status;
 return <article className="a-question" aria-label={`Question ${q.id}`} data-question-id={q.id}>
  <div className="a-question-kind">{q.status==='pending'?(q.required?'Your answer is needed':'Optional question'):q.status==='answered'?'Answered':'Closed question'}</div>
  <p className="a-question-prompt">{q.prompt}</p><p className="a-caption">{q.dependency}</p>
  {q.status==='pending'?<form onSubmit={submit} aria-label={`Answer: ${q.prompt}`}>
   <fieldset disabled={busy}><legend className="a-sr-only">{q.prompt}</legend>
    {q.options.map(choice=><label className="a-question-option" key={choice.id}>
     <input type="radio" name={`question-${q.id}`} value={choice.id} checked={option===choice.id} onChange={()=>{setOption(choice.id);setText('')}}/>
     <span><strong>{choice.label}</strong>{choice.description&&<small>{choice.description}</small>}</span>
    </label>)}
    {q.allowFreeText&&<label className="a-question-text">{q.options.length?'Or write your own answer':'Your answer'}<textarea aria-label={`Your answer: ${q.prompt}`} value={text} rows={2} maxLength={8000} onChange={event=>{setText(event.target.value);setOption('')}}/></label>}
   </fieldset>
   <div className="a-dialog-actions"><button className="a-primary" data-action="question.answer" disabled={busy||(!option&&!text.trim())}>{busy?'Saving answer…':'Submit answer'}</button>{!q.required&&<button className="a-soft" type="button" data-action="question.cancel" disabled={busy} onClick={skip}>Skip question</button>}</div>
   {q.required&&<p className="a-caption">This step waits for your answer. Other work can continue.</p>}
  </form>:<div className="a-question-result" role="status">
   {q.status==='answered'?<><p><strong>Your answer:</strong> {q.answer.text}</p><small>{delivery==='accepted'?'Answer saved and delivered to Amplifier.':delivery==='sending'?'Answer saved. Confirming delivery…':delivery==='rejected'?'Answer saved. Amplifier could not accept it.': 'Answer saved. Delivery is unconfirmed; it has not been sent again.'}</small>{q.delivery?.message&&delivery!=='accepted'&&<p>{q.delivery.message}</p>}</>:<small>{q.status==='superseded'?'Replaced by a newer question.':'Question cancelled without an answer.'}</small>}
  </div>}
  {error&&<p role="alert">{error}</p>}
 </article>;
}

export function activeQuestions(session){
 return (session?.questions||[]).filter(q=>q.status==='pending'||q.status==='answered'&&q.delivery?.status!=='accepted');
}

export function QuestionNotice({session,paneRef}){
 const active=activeQuestions(session);
 if(!active.length)return null;
 const pending=active.filter(q=>q.status==='pending').length;
 return <div className="a-question-notice" role="status"><span>{pending?`${pending===1?'A question needs':pending+' questions need'} your answer`:'An answer needs delivery confirmation'}</span><button type="button" className="a-link" onClick={()=>{const target=paneRef.current?.querySelector('[data-part="questions"]');if(target){target.scrollIntoView({block:'start'});target.focus({preventScroll:true})}}}>Review {pending?'questions':'answer'}</button></div>;
}

export function Questions({session,dispatch,history=false}){
 const active=activeQuestions(session);
 const questions=history?(session?.questions||[]).filter(q=>!active.includes(q)):active;
 if(!questions.length)return null;
 const cards=questions.map(q=><QuestionCard key={`${q.id}:${q.revision}`} question={q} sessionId={session.id} dispatch={dispatch}/>);
 return history?<details className="a-question-history"><summary>Answers and closed questions ({questions.length})</summary><div className="a-questions">{cards}</div></details>:<section tabIndex={-1} className="a-questions" data-part="questions" aria-label="Questions for you">{cards}</section>;
}
