/** 格式化投资专家页面使用的人民币金额。 */
export function investmentMoney(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

/** 格式化投资专家页面使用的证券价格。 */
export function investmentPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return value.toFixed(3)
}
