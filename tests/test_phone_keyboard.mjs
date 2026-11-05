import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const nodes = new Map(),
  timers = [];
function node(id) {
  if (!nodes.has(id))
    nodes.set(id, {
      value: "",
      listeners: {},
      disabled: false,
      addEventListener(name, fn) {
        this.listeners[name] = fn;
      },
      setSelectionRange() {},
      blur() {},
      focus() {
        this.focused = true;
      },
    });
  return nodes.get(id);
}
const source = fs
  .readFileSync(new URL("../frontend/public/phone.js", import.meta.url), "utf8")
  .replace(/^import[^\n]+\n/, "")
  .replace(/init\(\);\s*$/, "");
const context = vm.createContext({
  assert,
  console,
  navigator: {},
  URL,
  localStorage: { getItem: () => "", setItem() {} },
  sessionStorage: { getItem: () => "", setItem() {} },
  document: { querySelector: node, addEventListener() {} },
  window: { addEventListener() {} },
  setTimeout: (fn) => timers.push(fn),
  clearTimeout() {},
});
vm.runInContext(source, context);
vm.runInContext(
  `
  const sent=[];
  remoteView={channel:{readyState:'open'},text:t=>sent.push(['text',t]),key:k=>sent.push(['key',k]),close(){}};
  task={active:true,state:'WAITING_HUMAN'};
  document.querySelector('#open-keyboard').onclick();
  assert.equal(keyboard.focused,true);
  function emit(type,options={}) {
    const event={cancelable:true,preventDefault(){this.prevented=true;},...options};
    keyboard.listeners[type](event);return event;
  }
  emit('beforeinput',{inputType:'insertText',data:'a'});
  assert.equal(JSON.stringify(sent),JSON.stringify([['text','a']]));
  emit('beforeinput',{inputType:'deleteContentBackward'});
  emit('keydown',{key:'Tab'});
  emit('beforeinput',{inputType:'insertLineBreak'});
  assert.equal(JSON.stringify(sent.slice(-3)),JSON.stringify([['key','Backspace'],['key','Tab'],['key','Enter']]));
  emit('compositionstart');
  emit('beforeinput',{inputType:'insertText',data:'zhong',isComposing:true});
  emit('compositionend',{data:'中文'});
  emit('beforeinput',{inputType:'insertText',data:'中文'});
  assert.equal(sent.filter(x=>x[0]==='text'&&x[1]==='中文').length,1);
`,
  context,
);
timers.splice(0).forEach((fn) => fn());
vm.runInContext(
  `
  keyboard.value=keyboardMarker+'粘贴的文字';
  emit('input',{inputType:'insertFromPaste'});
  assert.equal(sent.at(-1)[1],'粘贴的文字');
  // Some mobile browsers do not allow beforeinput to be cancelled.
  emit('beforeinput',{inputType:'insertText',data:'b',cancelable:false});
  keyboard.value=keyboardMarker+'b';emit('input',{inputType:'insertText'});
  assert.equal(sent.filter(x=>x[0]==='text'&&x[1]==='b').length,1);
  keyboard.value='';emit('input',{inputType:'deleteContentBackward'});
  assert.equal(sent.at(-1)[1],'Backspace');
  closeRemote();assert.equal(keyboard.value,keyboardMarker);assert.equal(remoteView,null);
`,
  context,
);
console.log(
  "PASS: direct typing, Chinese IME, paste, deletion, enter, tab and disconnect cleanup",
);
