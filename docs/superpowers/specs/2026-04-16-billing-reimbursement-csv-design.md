# Billing Reimbursement CSV Design

## Goal

在 `/billing` 管理页新增一个“报销版 CSV 导出”按钮，导出内容只保留报销需要的月度汇总，不包含阶段、模型、请求次数等细节。

## Approved Scope

- 导出行为跟随当前页面分组模式。
- 当页面处于“按剧本”模式时，导出列为：`月份`、`剧本`、`用户`、`报销金额（元）`。
- 当页面处于“按用户”模式时，导出列为：`月份`、`用户`、`报销金额（元）`。
- 金额使用账单流水按月汇总后的净额，退款会抵扣当月金额。
- 导出文件面向报销使用，不展示阶段、模型、分类型费用、请求次数。

## Data Rules

- 月份按账单流水 `created_at` 归类，格式使用 `YYYY-MM`。
- 汇总时沿用现有 billing 过滤规则，继续排除测试账号数据。
- 只输出净额不为 `0` 的月度行，避免报销表里出现空行。
- 同一月份内的排序按名称和主键稳定排序，便于复核。

## UX

- 按钮放在 `/billing` 顶部操作区，和“价格规则”“刷新数据”同层。
- 点击后调用管理员鉴权下的新接口，拿到月度汇总数据后在前端生成 CSV。
- 下载文件使用 UTF-8 BOM，保证 Excel 打开中文不乱码。
- 下载文件名包含导出类型和日期，例如 `billing-reimbursement-script-2026-04-16.csv`。

## Implementation Shape

- `backend/billing_service.py` 新增月度报销汇总构建函数。
- `backend/main.py` 新增管理员接口，返回当前分组模式的报销导出数据。
- `frontend/billing.html` 新增导出按钮、导出请求和 CSV 拼装逻辑。
- `tests/test_billing_service.py` 覆盖月度汇总行为。
- `tests/test_billing_frontend.js` 覆盖按钮和导出 helper 的存在性。

## Out Of Scope

- 不改现有账单详情表。
- 不新增多文件 ZIP、Excel、双表同导等复杂导出形态。
- 不新增筛选月份范围。
