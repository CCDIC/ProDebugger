'use strict';
// Presentation-only localization. Source records, evidence and input values
// remain verbatim. No translation service or model call is involved.
(() => {
  const key = 'prodebugger.language';
  let language = 'en';
  try { if (localStorage.getItem(key) === 'zh') language = 'zh'; } catch (_) {}
  const originalText = new WeakMap(), originalAttributes = new WeakMap();
  const skip = 'script,style,pre,code,kbd,samp,textarea,input,[data-i18n-skip],[translate="no"],.i18n-raw,.session-event-preview,.session-value,.trace-task-full,.semantic-quote,blockquote,.analysis-code,.detail-code,.session-raw,.node-index';
  const attributes = ['title','aria-label','placeholder','alt'];
  let observer, scheduled = false;
  const generated = Object.fromEntries(Object.entries(window.ProDebuggerGeneratedTranslations||{}).map(([source,target])=>[source.trim(),String(target).trim()]));
  const translations = Object.freeze({...generated,...window.ProDebuggerTranslations});
  const dictionary = () => translations;
  function translate(text) {
    if (text == null) return '';
    const value = String(text);
    if (language === 'zh') return value;
    const left = value.match(/^\s*/)[0], right = value.match(/\s*$/)[0], core = value.trim();
    if (!core) return value;
    const exact = dictionary()[core];
    if (exact !== undefined) return left + exact + right;
    for (const [pattern, render] of (window.ProDebuggerTranslationPatterns || [])) {
      const match = core.match(pattern);
      if (match) { const rendered=render(match,translate); if(rendered!==core)return left+rendered+right; }
    }
    // UI combines titles and independently labelled statuses. Unknown titles
    // stay verbatim while recognized status segments use the interface language.
    if (/[·；]/.test(core)) {
      const parts=core.split(/(\s*[·；]\s*)/), translated=parts.map((part,index)=>index%2?part:translatePart(part));
      if (translated.some((part,index)=>part!==parts[index])) return left+translated.join('')+right;
    }
    return value;
  }
  function translatePart(value) {
    const trimmed=value.trim(), word=dictionary()[trimmed];
    if(word!==undefined)return value.replace(trimmed,word);
    for(const [pattern,render]of(window.ProDebuggerTranslationPatterns||[])){const m=trimmed.match(pattern);if(m){const rendered=render(m,translate);if(rendered!==trimmed)return rendered;}}
    return value;
  }
  function protectedNode(node) { return !!node?.parentElement?.closest(skip); }
  function refresh(root=document.body) {
    if(!root)return;
    observer?.disconnect();
    try {
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      let node;
      while((node=walker.nextNode())) {
        if(protectedNode(node))continue;
        const saved=originalText.get(node), current=node.nodeValue;
        const source=saved&&current===saved.rendered?saved.source:current;
        const rendered=translate(source);
        originalText.set(node,{source,rendered});
        if(current!==rendered)node.nodeValue=rendered;
      }
      const elements=[...(root.nodeType===Node.ELEMENT_NODE?[root]:[]),...root.querySelectorAll('[title],[aria-label],[placeholder],[alt]')];
      for(const element of elements){
        if(element.closest('[data-i18n-skip],[translate="no"],.i18n-raw,pre,code,.session-state-cell'))continue;
        const stored=originalAttributes.get(element)||{};
        for(const attr of attributes){
          if(!element.hasAttribute(attr))continue;
          const current=element.getAttribute(attr),saved=stored[attr],source=saved&&current===saved.rendered?saved.source:current;
          const rendered=translate(source);stored[attr]={source,rendered};if(current!==rendered)element.setAttribute(attr,rendered);
        }
        originalAttributes.set(element,stored);
      }
      document.documentElement.lang=language==='zh'?'zh-CN':'en';
      const selector=document.getElementById('language-select');if(selector)selector.value=language;
    } finally { observer?.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attributes}); }
  }
  function schedule(){if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;refresh();});}
  function setLanguage(value){
    if(!['en','zh'].includes(value))return;
    language=value;try{localStorage.setItem(key,value);}catch(_){}
    refresh();window.dispatchEvent(new CustomEvent('languagechange',{detail:{language:value}}));
  }
  window.I18n=Object.freeze({t:translate,setLanguage,refresh,get language(){return language;}});
  function start(){observer=new MutationObserver(schedule);refresh();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
