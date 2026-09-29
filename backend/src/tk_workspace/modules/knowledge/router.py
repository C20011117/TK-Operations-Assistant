from fastapi import APIRouter, HTTPException

from tk_workspace.modules.knowledge import service
from tk_workspace.modules.knowledge.schemas import (
    CategorySuggestion,
    CategorySuggestionsIn,
    DraftIn,
    ProductCreate,
    ProductDetail,
    ProductSummary,
    ProductUpdate,
    ProductVersion,
)

router = APIRouter(prefix="/products", tags=["products"])


def _run(fn, *args):
    try:
        return fn(*args)
    except service.ProductError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.extra}) from e


@router.get("", response_model=list[ProductSummary], summary="产品列表")
def products_index(include_archived: bool = False) -> list[ProductSummary]:
    return service.list_products(include_archived)


@router.post("", response_model=ProductDetail, status_code=201, summary="新建产品（同时生成空白草稿 v1）")
def products_create(body: ProductCreate) -> ProductDetail:
    return _run(service.create_product, body)


@router.post(
    "/category-suggestions",
    response_model=list[CategorySuggestion],
    summary="根据产品名称 / 品类词推荐 TikTok 商品类目（FastMoss，不扣费）",
)
def products_category_suggestions(body: CategorySuggestionsIn) -> list[CategorySuggestion]:
    return _run(service.suggest_categories, body.query)


@router.get("/{product_id}", response_model=ProductDetail, summary="产品详情（当前版本、草稿、版本历史）")
def products_detail(product_id: str) -> ProductDetail:
    return _run(service.get_product, product_id)


@router.put("/{product_id}", response_model=ProductDetail, summary="修改名称与 SKU")
def products_update(product_id: str, body: ProductUpdate) -> ProductDetail:
    return _run(service.update_product, product_id, body)


@router.post("/{product_id}/draft", response_model=ProductDetail, summary="以当前版本为底稿开始修改")
def products_start_draft(product_id: str) -> ProductDetail:
    return _run(service.start_draft_from_current, product_id)


@router.put("/{product_id}/draft", response_model=ProductDetail, summary="保存草稿")
def products_save_draft(product_id: str, body: DraftIn) -> ProductDetail:
    return _run(service.save_draft, product_id, body)


@router.delete("/{product_id}/draft", response_model=ProductDetail, summary="放弃草稿")
def products_discard_draft(product_id: str) -> ProductDetail:
    return _run(service.discard_draft, product_id)


@router.post("/{product_id}/draft/confirm", response_model=ProductDetail, summary="确认草稿为新版本")
def products_confirm_draft(product_id: str) -> ProductDetail:
    return _run(service.confirm_draft, product_id)


@router.post("/{product_id}/archive", response_model=ProductDetail, summary="归档")
def products_archive(product_id: str) -> ProductDetail:
    return _run(service.set_archived, product_id, True)


@router.post("/{product_id}/restore", response_model=ProductDetail, summary="取消归档")
def products_restore(product_id: str) -> ProductDetail:
    return _run(service.set_archived, product_id, False)


@router.get("/versions/{version_id}", response_model=ProductVersion, summary="查看某个历史版本")
def products_version(version_id: str) -> ProductVersion:
    return _run(service.get_version, version_id)
