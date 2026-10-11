"""Browse and edit tag ancestry in the real manager using isolated fictional data."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent


def main():
    with sync_playwright() as playwright:
        executables = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                       Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
                       Path(playwright.chromium.executable_path)]
        executable = next((str(path) for path in executables if path.exists()), None)
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content("<html><body><main id='mount'><div class='library-workspace'>题卡</div></main></body></html>")
        page.add_style_tag(path=str(ROOT / "library-tag-manager.css"))
        page.add_script_tag(path=str(ROOT / "library-tag-manager.js"))
        page.evaluate("""() => {
          window.calls=[]; window.failNext=false;
          window.catalogue={revision:10,groups:[{id:'g1',name:'我的课堂'}],tags:[
            {id:'functions',name:'函数',kind:'knowledge',parent_ids:[],direct_count:2,subtree_count:6},
            {id:'equations',name:'方程',kind:'knowledge',parent_ids:[],direct_count:1,subtree_count:4},
            {id:'geometry',name:'几何',kind:'knowledge',parent_ids:[],direct_count:0,subtree_count:0},
            {id:'quadratic',name:'二次函数与方程',kind:'knowledge',parent_ids:['functions','equations'],direct_count:1,subtree_count:3,related_method_ids:['completing']},
            {id:'minimum',name:'二次函数最小值',kind:'knowledge',parent_ids:['quadratic'],direct_count:2,subtree_count:2},
            {id:'proofs',name:'证明方法',kind:'method',parent_ids:[],direct_count:0,subtree_count:1},
            {id:'completing',name:'配方法',kind:'method',parent_ids:['proofs'],direct_count:1,subtree_count:1},
            {id:'lesson',name:'周末练习',kind:'custom',parent_ids:[],direct_count:0,subtree_count:0}
          ]};
          catalogue.tags.forEach(t=>{t.active=true;t.groups=[{id:'g1',name:'我的课堂'}];});
          window.manager=QBLibraryTagManager.create({mount:document.getElementById('mount'),
            loadCatalogue:async()=>structuredClone(catalogue),
            request:async(url,options)=>{calls.push({url,options});
              const match=url.match(/\\/tags\\/([^/]+)\\/relations$/);
              if(match){const tag=catalogue.tags.find(t=>t.id===match[1]);return {revision:catalogue.revision,tag:structuredClone(tag)};}
              return {operations:[]};},
            action:async(action,fields)=>{calls.push({action,fields});
              if(failNext){failNext=false;throw new Error('虚构版本冲突');}
              const tag=catalogue.tags.find(t=>t.id===fields.tag_id);
              if(action==='set_parents')tag.parent_ids=fields.parent_ids;
              if(action==='add_parent')tag.parent_ids.push(fields.parent_id);
              if(action==='remove_parent')tag.parent_ids=tag.parent_ids.filter(id=>id!==fields.parent_id);
              if(action==='move_parent')tag.parent_ids=tag.parent_ids.map(id=>id===fields.from_parent_id?fields.to_parent_id:id);
              if(action==='set_related_methods')tag.related_method_ids=fields.related_method_ids;
              catalogue.revision++;return {catalogue:structuredClone(catalogue)};},
            editEntity:(entity,record,defaults)=>calls.push({edit:entity,id:record?.id,defaults}),
            openTemplates:()=>{},clearCatalogue:async()=>false,deleteTag:async()=>false,browseTag:tag=>calls.push({browse:tag.id})});
          return manager.show();
        }""")
        # A personal group is flat and never creates parent/child relationships.
        page.locator('[data-group-id="g1"]').click()
        assert page.locator("#tagManagerList .tag-directory-row").count() == 8
        assert page.locator("#tagManagerLocation").is_hidden()
        page.locator('[data-hierarchy-kind="knowledge"]').click()
        roots = page.locator("#tagManagerList .tag-directory-row").evaluate_all("rows=>rows.map(row=>row.dataset.tagId)")
        assert set(roots) == {"functions", "equations", "geometry"}
        page.locator('[data-tag-id="functions"] .tag-directory-usage').evaluate("el=>window.usageTitle=el.title")
        assert "本级 2 题" in page.evaluate("usageTitle") and "包含下级 6 题" in page.evaluate("usageTitle")
        before = page.evaluate("calls.filter(c=>c.action).length")
        page.locator('[data-expand-tag-id="functions"]').click()
        assert page.locator("#tagManagerList .tag-directory-row").count() == 1
        page.locator('[data-tag-id="quadratic"] .tag-directory-name').click()
        assert page.locator("#tagManagerDetail h2").inner_text() == "二次函数与方程"
        assert page.locator("#tagManagerRelations [data-parent-id]").count() == 2
        assert page.locator('[data-related-method-id="completing"]').is_visible()
        assert page.evaluate("calls.filter(c=>c.action).length") == before
        page.locator("#tagManagerCreateChild").click()
        assert page.evaluate("calls.find(c=>c.edit).defaults") == {"parent_ids": ["quadratic"], "kind": "knowledge"}
        page.locator("#tagManagerRelations").get_by_role("button", name="设置父级", exact=True).click()
        choices = page.locator(".tag-directory-relation-options input").evaluate_all("els=>els.map(el=>el.value)")
        assert "quadratic" not in choices and "minimum" not in choices and "completing" not in choices
        assert {"functions", "equations", "geometry"}.issubset(set(choices))
        # Editing the choice is inert until the explicit save, and conflict keeps it checked.
        page.locator('.tag-directory-relation-options input[value="geometry"]').check()
        assert page.evaluate("catalogue.tags.find(t=>t.id==='quadratic').parent_ids.length") == 2
        page.evaluate("failNext=true")
        page.locator("#tagManagerRelationSave").click()
        page.wait_for_function("!document.querySelector('#tagManagerRelationSave').disabled")
        assert page.locator('.tag-directory-relation-options input[value="geometry"]').is_checked()
        assert page.locator(".tag-directory-relation-dialog").is_visible()
        page.locator("#tagManagerRelationSave").click()
        page.wait_for_function("!document.querySelector('.tag-directory-relation-dialog')")
        assert page.evaluate("catalogue.tags.find(t=>t.id==='quadratic').parent_ids") == ["functions", "equations", "geometry"]
        assert page.evaluate("catalogue.tags.find(t=>t.id==='quadratic').groups[0].id") == "g1"
        # Remove one edge without removing a label or its other parent edges.
        page.locator('[data-parent-id="geometry"]').get_by_role("button", name="移除", exact=True).click()
        page.wait_for_function("!document.querySelector('#tagManagerWorkspace').getAttribute('aria-busy') || document.querySelector('#tagManagerWorkspace').getAttribute('aria-busy')==='false'")
        assert page.evaluate("catalogue.tags.find(t=>t.id==='quadratic').parent_ids") == ["functions", "equations"]
        # A move acts on exactly the named edge, preserving the other parent.
        page.locator('[data-parent-id="functions"]').get_by_role("button", name="移到…", exact=True).click()
        page.locator('.tag-directory-relation-options input[value="geometry"]').check()
        page.locator("#tagManagerRelationSave").click()
        page.wait_for_function("!document.querySelector('.tag-directory-relation-dialog')")
        assert page.evaluate("calls.filter(c=>c.action==='move_parent').at(-1).fields") == {"tag_id": "quadratic", "from_parent_id": "functions", "to_parent_id": "geometry"}
        assert page.evaluate("catalogue.tags.find(t=>t.id==='quadratic').parent_ids") == ["geometry", "equations"]
        # Multi-parent ancestry has two independently browsable breadcrumb paths.
        page.locator("#tagManagerRelations").get_by_role("button", name="查看层级", exact=True).click()
        assert page.locator("#tagManagerLocation .tag-directory-path").count() == 2
        assert page.locator('[data-tag-id="minimum"]').is_visible()
        page.locator("#tagManagerRelations").get_by_role("button", name="设置相关方法", exact=True).click()
        method_ids = page.locator(".tag-directory-relation-options input").evaluate_all("els=>els.map(el=>el.value)")
        assert set(method_ids) == {"proofs", "completing"}
        page.locator('.tag-directory-relation-options input[value="proofs"]').check()
        page.locator("#tagManagerRelationSave").click()
        page.wait_for_function("!document.querySelector('.tag-directory-relation-dialog')")
        assert page.evaluate("calls.filter(c=>c.action==='set_related_methods').at(-1).fields") == {"tag_id": "quadratic", "related_method_ids": ["completing", "proofs"]}
        # Search a whole kind even when located on a branch, while ignoring IME composition.
        page.locator("#tagManagerSearch").fill("函数")
        assert page.locator("#tagManagerList .tag-directory-row").count() == 3
        page.locator("#tagManagerSearch").evaluate("el=>{el.value='配方';el.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));}")
        assert page.locator("#tagManagerList .tag-directory-row").count() == 3
        page.locator("#tagManagerSearch").evaluate("el=>el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}))")
        assert page.locator("#tagManagerList .tag-directory-row").count() == 0
        page.locator("#tagManagerSearch").fill("函数")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator('[data-group-id="all"]').click()
        page.locator("#tagManagerSearch").fill("")
        page.locator('[data-tag-id="geometry"] .tag-directory-name').click()
        page.locator("#tagManagerAddParent").click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.querySelector('.tag-directory-relation-dialog')")
        assert page.locator(".tag-directory-relation-dialog").count() == 0
        assert not errors, json.dumps(errors, ensure_ascii=False)
        browser.close()
    print("Hierarchy manager browser checks passed: independent groups, DAG paths, any-level tags, explicit relation saves, cycle prevention, conflict retention, exact-edge move, related methods, counts, IME-safe search, and narrow layout.")


if __name__ == "__main__":
    main()
