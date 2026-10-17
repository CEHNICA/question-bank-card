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
          window.catalogue = {revision:1,tags:[
            {id:'s1',name:'初中 · 函数',kind:'knowledge',description:'教材章节，用来给标签目录分层。',aliases:[],active:true,count:5,parent_ids:[],child_ids:['t1','t2']},
            {id:'t2',name:'三角函数',kind:'knowledge',active:true,count:2,parent_ids:['s1','s3'],child_ids:[]},
            {id:'t1',name:'正弦函数',kind:'knowledge',description:'函数性质',aliases:['sin'],active:true,count:3,parent_ids:['s1'],child_ids:[]},
            {id:'s3',name:'高中 · 函数与方程',kind:'knowledge',description:'教材章节，用来给标签目录分层。',aliases:[],active:true,count:4,parent_ids:[],child_ids:['t2']},
            {id:'t3',name:'空间向量法',kind:'method',active:true,count:1,parent_ids:[],child_ids:[]},
            {id:'t4',name:'函数图像',kind:'knowledge',active:true,count:0,parent_ids:[],child_ids:[]}
          ]};
          window.manager = QBLibraryTagManager.create({mount:document.getElementById('mount'),
            loadCatalogue:async()=>structuredClone(catalogue),
            request:async(url,options)=>{calls.push({url,options}); return url.includes('/impact')?
              {token:'fictional-impact',question_count:4,historical_count:1}:{operations:[]};},
            action:async(action,fields)=>{
              calls.push({action,fields});
              if (waitNext) { waitNext=false; await new Promise(resolve=>window.releaseSave=resolve); }
              if (failNext) { failNext=false; throw new Error('虚构并发冲突'); }
              if(action==='add_parent'){const t=catalogue.tags.find(x=>x.id===fields.tag_id);
                if(!t.parent_ids.includes(fields.parent_id))t.parent_ids=[...t.parent_ids,fields.parent_id];}
              else if(action==='delete_tags') catalogue.tags=catalogue.tags.filter(t=>!fields.tag_ids.includes(t.id));
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
        # No group folder survives anywhere in the page.
        assert page.locator("#tagManagerGroups").count() == 0
        assert page.locator("#tagManagerGroupActions").count() == 0
        assert page.locator("#tagManagerCreateGroup").count() == 0
        assert page.locator("#tagManagerBulkGroup").count() == 0
        assert page.locator("#tagManagerBulkAdd").count() == 0
        assert page.locator("#tagManagerBulkRemove").count() == 0
        assert page.locator("#tagManagerHierarchyNav button").count() == 4
        assert page.locator("#tagManagerScopes button").count() == 2
        assert page.get_by_text("个人分组").count() == 0
        # A level shows its top-level tags, and the chapter opens into its children.
        page.locator('#tagManagerHierarchyNav [data-nav-id="knowledge"]').click()
        assert page.locator('#tagManagerTreeNav [data-tree-tag-id="s1"]').is_visible()
        assert page.locator("#tagManagerTreeNav").inner_text().count("三角函数") == 0
        page.locator('[data-tree-tag-id="s1"] .tag-directory-tree-name').click()
        assert page.locator("#tagManagerList .tag-directory-row").count() == 2
        assert page.locator('[data-tag-id="t1"]').is_visible()
        assert page.locator('[data-tag-id="t2"]').is_visible()
        assert page.locator("#tagManagerLocation").is_visible()
        # Clicking the level you are in steps up to its top row, not all the way out.
        page.locator('#tagManagerHierarchyNav [data-nav-id="knowledge"]').click()
        assert page.locator("#tagManagerList .tag-directory-row").count() == 3
        assert page.locator("#tagManagerLocation").inner_text().count("顶级") == 1
        page.locator('[data-tree-tag-id="s1"] .tag-directory-tree-name').click()

        def check(tag_id):
            page.locator(f'[data-tag-id="{tag_id}"] > input').check()

        check("t1")
        assert page.locator("#tagManagerDetail h2").inner_text() == "正弦函数"
        check("t2")
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 2 个"
        page.locator("#tagManagerSearch").fill("ＳＩＮ")
        assert page.locator("#tagManagerList .tag-directory-row").count() == 1
        assert page.locator('[data-tag-id="t1"]').is_visible()
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 2 个"
        page.locator("#tagManagerSearch").fill("")
        # A conflict must keep the precise selection and leave the saved tree untouched.
        page.locator('[data-tag-id="t1"] .tag-directory-name').click()
        page.evaluate("failNext=true")
        page.locator("#tagManagerAddParent").click()
        page.locator('.tag-directory-relation-option input[value="s3"]').check()
        page.evaluate("waitNext=true")
        page.locator("#tagManagerRelationSave").click()
        assert page.locator("#tagManagerRelationSave").is_disabled()
        page.evaluate("releaseSave()")
        page.wait_for_function("document.querySelector('#tagManagerStatus').classList.contains('error')")
        assert page.locator("#tagManagerDetail h2").inner_text() == "正弦函数"
        assert page.locator(".tag-directory-relation-dialog").count() == 1
        assert page.evaluate("catalogue.tags.find(t=>t.id==='t1').parent_ids.join()==='s1'")
        assert page.evaluate("calls.filter(c=>c.action==='add_parent').length") == 1
        page.locator("#tagManagerRelationSave").click()
        page.wait_for_function("!document.querySelector('.tag-directory-relation-dialog')")
        assert page.evaluate("calls.filter(c=>c.action==='add_parent').length") == 2
        assert page.evaluate("catalogue.tags.find(t=>t.id==='t1').parent_ids.includes('s3')")
        # Multi-select merge retains one stable target; impact and mutation share source IDs.
        check("t2")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#tagManagerBulkMerge").click()
        page.locator("#tagManagerMergeTarget").select_option("t1")
        page.locator("#tagManagerMergeSubmit").click()
        page.wait_for_function("!document.querySelector('.tag-directory-merge-dialog')")
        merged = page.evaluate("calls.find(c=>c.action==='merge_tags_batch').fields")
        assert merged == {"source_ids": ["t2"], "target_id": "t1", "impact_token": "fictional-impact", "confirmed": True}
        assert page.locator("#tagManagerBulk strong").inner_text() == "已选 1 个"
        # Selection may span levels, but deletion only touches explicitly checked IDs.
        page.locator('#tagManagerHierarchyNav [data-nav-id="knowledge"]').click()
        check("t4")
        page.locator("#tagManagerBulkDelete").click()
        page.wait_for_function("!document.querySelector('[data-tag-id=\"t1\"]')")
        deleted = page.evaluate("calls.find(c=>c.action==='delete_tags').fields")
        assert set(deleted["tag_ids"]) == {"t1", "t4"}
        # A method tag is a level of its own and is untouched by a knowledge-level batch.
        page.locator('#tagManagerHierarchyNav [data-nav-id="method"]').click()
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
    print("Tag-directory workspace browser checks passed: no group folder, level tree, precise batch scope, save locking, conflict retention, merge, delete, search, keyboard, undo, mobile and scroll restoration.")


if __name__ == "__main__":
    main()
