# Billing Month Filter Design

## Goal

在 `/billing` 页面新增按月份筛选，并让导出 CSV 严格跟随当前选中的月份。月份归类需要和前端看到的本地日期一致，避免出现“页面看到是 4 月、导出却被算到别的月份”的情况。

## Approved Scope

- 页面顶部新增月份筛选控件，支持“全部月份”和指定 `YYYY-MM`。
- 当前月份筛选会同时作用于：
  - 用户列表
  - 剧本列表
  - 剧本详情
  - 剧集详情
  - 报销 CSV 导出
- 前端选中 `2026-04` 时，导出只能包含 2026 年 4 月的数据。
- 导出文件名使用所选月份，而不是当前系统日期。

## Date Rule

- 账单流水底层仍使用 `created_at`。
- 月份归类统一按“前端展示语义”处理：
  - 如果后端时间对象没有时区，则按 UTC 写入时间解释；
  - 再转换到 `Asia/Shanghai`；
  - 最后取 `YYYY-MM` 作为筛选与导出月份。
- 页面时间展示和导出月份必须使用同一套时间解释规则。

## Backend Shape

- `backend/billing_service.py`
  - 新增月份标准化 helper。
  - `_query_billing_entries` 支持 `month` 过滤。
  - 列表、详情、报销导出都复用同一份 month 过滤逻辑。
- `backend/main.py`
  - 相关 `/api/billing/*` 路由支持 `month` query 参数。

## Frontend Shape

- `frontend/billing.html`
  - 新增月份选择器和清空筛选按钮。
  - 所有加载请求都携带当前月份。
  - CSV 文件名改为 `billing-reimbursement-<group>-YYYY-MM.csv`。
  - CSV 内容仍保持“剧本 / 用户”月度报销格式，但只输出所选月份。

## Testing

- `tests/test_billing_service.py`
  - 覆盖 month 参数过滤。
  - 覆盖上海月份归类。
- `tests/test_billing_frontend.js`
  - 覆盖月份筛选控件与月份参数透传。
  - 覆盖文件名使用选中月份。

## Out Of Scope

- 不新增跨月区间筛选。
- 不修改账单金额计算规则。
- 不引入额外前端框架或重做页面布局。
