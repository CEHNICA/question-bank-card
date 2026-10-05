"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
// 1.12.7：收侧栏的开关是侧栏接缝上那个小按钮，从头到尾待在原地，只换图标和说明。
// 它不再在顶栏和抽屉之间来回搬 —— 搬来搬去用户根本记不住它在哪。
class Element {
  constructor(){this.children=[];this.events={};this.attrs={};this.title="";}
  append(child){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(value=>value!==child);this.children.push(child);child.parentNode=this;}
  setAttribute(key,value){this[key]=value;this.attrs[key]=value;}
  addEventListener(key,callback){this.events[key]=callback;}
  focus(){document.activeElement=this;}
  querySelector(selector){return selector==="use"?this.glyph:null;}
}
const rail=new Element(), tools=new Element(), source=new Element(), classes=new Set();
tools.append(source);
const button=new Element();
button.glyph=new Element();
rail.append(button);
const document={activeElement:null,querySelector:()=>null,body:{classList:{contains:key=>classes.has(key),toggle:(key,value)=>value?classes.add(key):classes.delete(key)}}};
const root={document};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"library-question-viewer.js"),"utf8"),{window:root});
const mounted=root.LibraryQuestionViewer.mountFocus({button});
const set=mounted.set;
assert.equal(mounted.button,button,"The seam button itself is wired, not a replacement one");
assert.equal(button.parentNode,rail,"It never leaves the rail seam");
set(false);
assert.equal(button["aria-expanded"],"true"); assert.equal(button.title,"收起筛选栏");
assert.equal(button.glyph["href"],"#i-chev-left");
button.focus(); set(true);
assert(document.activeElement===button,"Focus stays on the button after the click");
assert(document.body.classList.contains("library-focus-mode"));
assert.equal(button.parentNode,rail,"Focus mode must not move the button into the header");
assert.equal(button["aria-expanded"],"false"); assert.equal(button.title,"展开筛选栏");
assert.equal(button.glyph["href"],"#i-chev-right","Collapsed rail points the other way");
set(false);
assert(!document.body.classList.contains("library-focus-mode"));
assert.equal(button.glyph["href"],"#i-chev-left");
set(true);set(true);
assert.deepEqual(tools.children,[source],"The header tool area is left alone entirely");
console.log("Rail seam toggle stays put, swaps its icon and keeps keyboard focus: OK");
