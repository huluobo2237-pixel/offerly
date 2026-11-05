"""Set a known checkbox once and verify its live state, including label wrappers."""

import json

SCRIPT = """async (desired) => {
 const label=this.closest('label');
 let input=this.matches('input[type=checkbox]')?this:label?.control;
 if(!input){const root=label||this;const nodes=root.querySelectorAll('input[type=checkbox]');if(nodes.length===1)input=nodes[0];}
 if(!input||input.type!=='checkbox')return {recognized:false};
 if(input.disabled)return {recognized:true,error:'复选框不可用，请请求用户协助。'};
 const changed=input.checked!==desired;
 if(changed)input.click();
 await new Promise(resolve=>setTimeout(resolve,80));
 return {recognized:true,checked:input.checked,changed,success:input.checked===desired,
 buttons:[...document.querySelectorAll('button,input[type=submit]')].filter(e=>e.getBoundingClientRect().width).map(e=>({text:(e.innerText||e.value||'').trim().slice(0,60),disabled:e.disabled})).slice(0,30)};
}"""


async def set_checkbox(browser, index, checked=True):
    node = (await browser.get_selector_map()).get(index)
    if not node:
        return {"recognized": False, "error": "元素编号已失效，请使用当前页面编号。"}
    page = await browser.get_current_page()
    if not page:
        return {"recognized": False, "error": "当前没有可操作的网页。"}
    session = await page._ensure_session()
    await page._client.send.DOM.getDocument(session_id=session)
    element = await page.get_element(node.backend_node_id)
    result = await element.evaluate(SCRIPT, checked)
    return json.loads(result) if isinstance(result, str) else result
