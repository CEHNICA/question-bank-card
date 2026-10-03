"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
class Element {
  constructor(){this.children=[];this.events={};}
  append(child){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(value=>value!==child);this.children.push(child);child.parentNode=this;}
  setAttribute(key,value){this[key]=value;}
  addEventListener(key,callback){this.events[key]=callback;}
  focus(){document.activeElement=this;}
}
const tools=new Element(), source=new Element(), host=new Element(), classes=new Set(); tools.append(source);
const document={activeElement:null,querySelector:selector=>selector===".topbar-tools"?tools:null,body:{classList:{contains:key=>classes.has(key),toggle:(key,value)=>value?classes.add(key):classes.delete(key)}}};
const root={document};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"library-question-viewer.js"),"utf8"),{window:root});
const {button,set}=root.LibraryQuestionViewer.mountFocus({node:()=>new Element(),host});
assert.equal(button.parentNode,host); button.focus(); set(true);
assert.equal(button.parentNode,tools); assert.equal(document.activeElement,button); assert.equal(button.textContent,"退出专注浏览");
assert.equal(tools.children[0],source,"Existing header tools keep their original place");
set(false);assert.equal(button.parentNode,host);assert.deepEqual(tools.children,[source]);assert.equal(button.textContent,"专注浏览");
set(true);set(true);assert.equal(tools.children.filter(value=>value===button).length,1,"Repeated calls do not duplicate the exit button");
console.log("Focused browsing exit lives in header, returns to original host and keeps keyboard focus: OK");
