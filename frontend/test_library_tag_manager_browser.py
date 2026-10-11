"""Exercise the directory workspace with fictional data, without a server or AI."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent


def main():
    with sync_playwright() as playwright:
        candidates = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                      Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
                      Path(playwright.chromium.executable_path)]
        executable = next((str(path) for path in candidates if path.exists()), None)
        browser = playwright.chromium.launch(headless=True,
            **({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content("""<html><head></head><body><nav id='mainNav'>正式题库 · 设置</nav>
          <main id='mount'><div class='library-workspace' style='height:900px'>现有题卡</div></main>
          <footer style='height:400px'></footer></body></html>""")
        page.add_style_tag(path=str(ROOT / "library-tag-manager.css"))
        page.add_script_tag(path=str(ROOT / "library-tag-manager.js"))
        page.evaluate("""() => {
          window.calls = []; window.failNext = false; window.waitNext = false;
          window.catalogue = {revision:1,groups:[{id:'g1',name:'初中数学'},{id:'g2',name:'我的课堂'}],tags:[
            {id:'t1',name:'正弦函数',kind:'knowledge',description:'函数性质',aliases:['sin'],active:true,count:3,groups:[{id:'g1',name:'初中数学'}]},
            {id:'t2',name:'三角函数',kind:'knowledge',active:true,count:2,groups:[]},
            {id:'t3',name:'空间向量法',kind:'method',active:true,count:1,groups:[]},
            {id:'t4',name:'函数图像',kind:'knowledge',active:true,count:0,groups:[]}
          ]};
          window.manager = QBLibraryTagManager.create({mount:document.getElementById('mount'),
            loadCatalogue:async()=>structuredClone(catalogue),
            request:async(url,options)=>{calls.push({url,options}); return url.includes('/impact')?
              {token:'fictional-impact',question_count:4,historical_count:1}:{operations:[]};},
            action:async(action,fields)=>{
              calls.push({action,fields});
              if (waitNext) { waitNext=false; await new Promise(resolve=>window.releaseSave=resolve); }
              if (failNext) { failNext=false; throw new Error('虚构并发冲突'); }
              if(action==='group_membership_batch') {
                catalogue.tags.filter(t=>fields.tag_ids.includes(t.id)).forEach(t=>{
                  t.groups=t.groups.filter(g=>g.id!==fields.group_id);
                  if(fields.mode==='add')t.groups.push(catalogue.groups.find(g=>g.id===fields.group_id));
                });
              } else if(action==='delete_tags') catalogue.tags=catalogue.tags.filter(t=>!fields.tag_ids.includes(t.id));
              else if(action==='merge_tags_batch')catalogue.tags=catalogue.tags.filter(t=>!fields.source_ids.includes(t.id));
              catalogue.revision++;return {catalogue:structuredClone(catalogue)};
            },
            editEntity:(entity,record,context)=>calls.push({edit:entity,id:record?.id,context}),
            openTemplates:()=>calls.push({template:true}),clearCatalogue:async()=>false,
            deleteTag:async()=>false,browseTag:(tag)=>calls.push({browse:tag.id}),
            onClose:()=>calls.push({close:true})});
          return manager.show();
        }""")
        assert page.locator("#mainNav").is_visible()
        assert page.locator(".library-workspace").is_hidden()
        assert page.locator("#tagManagerWorkspace").is_visible()
        assert page.locator("#tagManagerGroups button").count() == 5
        assert page.locator("#tagManagerGroupActions").locator("button").count() == 0
        page.locator('[data-group-id="g1"]').click()
        assert page.locator("#tagManagerGroupActions").get_by_role("button", name="删除分组（保留标签）").is_visible()
        page.locator('[data-group-id="all"]').click()

        def check(tag_id):
            page.locator(f'[data-tag-id="{tag_id}"] > input').check()

        check("t1")
        assert page.locator("#tagManagerDetail h2").inner_text() == "正弦函数"
        check("t2")
        page.locator("#tagManagerBulkGroup").select_option("g2")
        page.evaluate("waitNext=true")
        page.locator("#tagManagerBulkAdd").click()
        assert page.locator("#tagManagerBulkAdd").is_disabled()
        assert page.locator("#tagManagerBack").is_disabled()
        page.evaluate("releaseSave()")
        page.wait_for_function("!document.querySelector('#tagManagerBulkAdd').disabled")
        assert page.evaluate("catalogue.tags.filter(t=>['t1','t2'].includes(t.id)).every(t=>t.groups.some(g=>g.id==='g2'))")
        assert page.evaluate("calls.filter(c=>c.action==='group_membership_batch').length") == 1
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 2 个"
        page.locator("#tagManagerSearch").fill("ＳＩＮ")
        assert page.locator("#tagManagerList .tag-directory-row").count() == 1
        assert page.locator('[data-tag-id="t1"]').is_visible()
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 2 个"
        page.locator("#tagManagerSearch").fill("")
        # A conflict must keep the precise selection and leave the saved membership untouched.
        page.evaluate("failNext=true")
        page.locator("#tagManagerBulkRemove").click()
        page.wait_for_function("document.querySelector('#tagManagerStatus').classList.contains('error')")
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 2 个"
        assert page.evaluate("catalogue.tags.filter(t=>['t1','t2'].includes(t.id)).every(t=>t.groups.some(g=>g.id==='g2'))")
        page.locator("#tagManagerBulkRemove").click()
        page.wait_for_function("!document.querySelector('#tagManagerBulkRemove').disabled")
        assert page.evaluate("catalogue.tags.filter(t=>['t1','t2'].includes(t.id)).every(t=>!t.groups.some(g=>g.id==='g2'))")
        # Multi-select merge retains one stable target; impact and mutation share source IDs.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#tagManagerBulkMerge").click()
        page.locator("#tagManagerMergeTarget").select_option("t1")
        page.locator("#tagManagerMergeSubmit").click()
        page.wait_for_function("!document.querySelector('.tag-directory-merge-dialog')")
        merged = page.evaluate("calls.find(c=>c.action==='merge_tags_batch').fields")
        assert merged == {"source_ids": ["t2"], "target_id": "t1", "impact_token": "fictional-impact", "confirmed": True}
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 1 个"
        # Selection may span groups, but deletion only touches explicitly checked IDs.
        check("t4")
        page.locator("#tagManagerBulkDelete").click()
        page.wait_for_function("!document.querySelector('[data-tag-id=\"t1\"]')")
        deleted = page.evaluate("calls.find(c=>c.action==='delete_tags').fields")
        assert set(deleted["tag_ids"]) == {"t1", "t4"}
        assert page.locator('[data-tag-id="t3"]').count() == 1
        # Searching includes aliases and does not recreate the search input or disturb focus.
        page.locator("#tagManagerSearch").fill("不存在")
        assert page.locator(".tag-directory-empty strong").inner_text() == "没有找到这个标签"
        assert page.locator("#tagManagerSearch").evaluate("el=>el===document.activeElement")
        page.locator("#tagManagerSearch").fill("")
        page.locator('[data-tag-id="t3"] .tag-directory-name').click()
        page.locator("#tagManagerEditTag").click()
        assert page.evaluate("calls.find(c=>c.edit==='tag').id") == "t3"
        # Used-question navigation returns to the same card view; mobile never overflows horizontally.
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
        page.locator('[data-tag-id="t3"] .tag-directory-usage').click()
        assert page.locator("#tagManagerWorkspace").is_hidden()
        assert page.locator(".library-workspace").is_visible()
        assert page.evaluate("calls.find(c=>c.browse).browse") == "t3"
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.evaluate("window.scrollTo(0,200)")
        before = page.evaluate("window.scrollY")
        page.evaluate("manager.show()")
        page.locator('[data-tag-id="t3"] .tag-directory-name').click()
        page.keyboard.press("Control+a")
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 1 个"
        page.keyboard.press("Escape")
        assert page.locator("#tagManagerWorkspace").is_visible()
        assert page.locator("#tagManagerBulk").is_hidden()
        page.evaluate("manager.renderHistory([{id:'op1',action:'catalogue:delete_tags',created_at:new Date().toISOString(),can_undo:true},{id:'op2',action:'catalogue:update_tag',created_at:new Date().toISOString(),can_undo:false}])")
        page.locator(".tag-directory-history > summary").click()
        assert page.locator("#tagManagerHistoryList button").nth(1).is_disabled()
        page.locator("#tagManagerHistoryList button").first.click()
        page.wait_for_function("!document.querySelector('#tagManagerWorkspace').getAttribute('aria-busy') || document.querySelector('#tagManagerWorkspace').getAttribute('aria-busy')==='false'")
        assert page.evaluate("calls.some(c=>c.url==='/api/library/tags/undo'&&JSON.parse(c.options.body).operation_id==='op1')")
        page.locator("#tagManagerBack").click()
        assert page.evaluate("Math.abs(window.scrollY-" + str(before) + ") < 2")
        assert not errors, json.dumps(errors, ensure_ascii=False)
        browser.close()
    print("Tag-directory workspace browser checks passed: page, contextual details, precise batch scope, save locking, conflict retention, merge, delete, search, keyboard, undo, mobile and scroll restoration.")


if __name__ == "__main__":
    main()
