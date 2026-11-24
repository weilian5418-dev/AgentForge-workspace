import type { FileChangeViewModel } from '@/domain/view-models'

export function contentForFile(file: FileChangeViewModel | undefined, fallback: { original:string; modified:string }) {
  if (!file || file.language === 'html') return fallback
  if (file.name === 'export.ts') return {
    original: 'export interface OrderFilters {\n  status?: string\n}',
    modified: 'export interface OrderFilters {\n  status?: string\n  start_date?: string\n  end_date?: string\n}\n\nexport async function downloadOrders(filters: OrderFilters) {\n  const query = new URLSearchParams()\n  Object.entries(filters).forEach(([key, value]) => {\n    if (value) query.set(key, value)\n  })\n  const response = await fetch(`/api/orders/export?${query}`)\n  if (!response.ok) throw new Error("订单导出失败")\n  return response.blob()\n}',
  }
  if (file.name === 'test_export.py') return {
    original: '',
    modified: 'def test_export_respects_filters(client):\n    response = client.get("/api/orders/export?status=paid")\n    assert response.status_code == 200\n    assert response.headers["content-type"].startswith(\n        "application/vnd.openxmlformats"\n    )\n\ndef test_export_requires_permission(client):\n    response = client.get("/api/orders/export", user=None)\n    assert response.status_code == 401',
  }
  return {
    original: '# Order routes\n',
    modified: 'from io import BytesIO\nfrom openpyxl import Workbook\n\ndef export_orders(filters, current_user):\n    require_permission(current_user, "orders:export")\n    orders = query_orders(filters)\n    workbook = Workbook()\n    sheet = workbook.active\n    sheet.append(["订单号", "金额", "状态", "创建时间"])\n    for order in orders:\n        sheet.append([order.id, order.amount,\n                      order.status, order.created_at])\n    output = BytesIO()\n    workbook.save(output)\n    return output.getvalue()',
  }
}
