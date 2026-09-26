from django.urls import path

from core import views

urlpatterns = [
    path("", views.index_page),
    path("library", views.library_page),
    path("app.js", views.app_script),
    path("qb-render.js", views.render_script),
    path("library.js", views.library_script),
    path("styles.css", views.styles),
    path("library.css", views.library_styles),
    path("vendor/katex/<path:asset>", views.katex_asset),
    path("api/health", views.health),
    path("api/status", views.status),
    path("api/papers", views.papers),
    path("api/papers/<uuid:paper_id>", views.paper_detail),
    path("api/papers/<uuid:paper_id>/pages/<int:page>/preview", views.page_preview),
    path("api/papers/<uuid:paper_id>/retry", views.paper_retry),
    path("api/papers/<uuid:paper_id>/resegment", views.paper_resegment),
    path("api/papers/<uuid:paper_id>/page-order", views.paper_page_order),
    path("api/papers/<uuid:paper_id>/approve-green", views.approve_green),
    path("api/papers/<uuid:paper_id>/publish", views.publish_paper),
    path("api/papers/<uuid:paper_id>/questions", views.add_question),
    # 正式题库页面沿用 M3 的地址查看出处
    path("api/documents/<uuid:paper_id>/pages/<int:page>/preview", views.page_preview),
    path("api/questions/<int:question_id>", views.question_delete),
    path("api/questions/<int:question_id>/figures/<int:index>", views.question_figure),
    path("api/questions/<int:question_id>/<str:action>", views.question_action),
    path("api/m3/papers", views.m3_papers),
    path("api/library", views.library_list),
    path("api/library/<uuid:publication_id>", views.library_detail),
    path("api/library/<uuid:publication_id>/figures/<str:name>", views.library_figure),
    path("api/library/<uuid:publication_id>/withdraw", views.library_withdraw),
]
