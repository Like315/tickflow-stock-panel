const STATUS_LABELS: Record<string, string> = {
  promoted: '已晋升',
  rejected: '未通过',
  inconclusive: '未形成结论',
  shadow: '未启用',
  champion: '冠军策略',
  candidate: '候选策略',
  retired: '已退役',
  running: '运行中',
  succeeded: '已完成',
  interrupted: '已中断',
  failed: '失败',
  pending: '等待中',
  blocked: '已阻止',
  reused: '已复用',
  started: '已启动',
  stopped: '已停止',
  idle: '空闲',
  degraded: '降级运行',
  submitted: '已提交',
  dataset_bootstrap_submitted: '已提交样本构建',
  skipped_after_risk_trip: '触发风控后已跳过',
  not_configured: '未配置',
  live: '实时',
  snapshot: '历史快照',
  ready: '已就绪',
  queued: '排队中',
  processing: '处理中',
  unavailable: '不可用',
  cancelled: '已取消',
  canceled: '已取消',
}

const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  promoted: '已晋升',
  rejected: '未通过',
  inconclusive: '未形成结论',
  shadow: '未形成结论',
}

const TASK_LABELS: Record<string, string> = {
  evolution: '策略进化评估',
  dataset_bootstrap: '构建训练样本',
  model_training: '训练决策模型',
  strategy_optimization: '回测优化当前内置策略',
  strategy_generation: '生成并验证专家策略',
}

export function investmentExpertStatusLabel(status: string | null | undefined): string {
  if (!status) return '--'
  return STATUS_LABELS[status] ?? '未知状态'
}

export function investmentExpertExperimentStatusLabel(status: string | null | undefined): string {
  if (!status) return '--'
  return EXPERIMENT_STATUS_LABELS[status] ?? '未知实验结果'
}

export function investmentExpertTaskLabel(task: string | null | undefined): string {
  if (!task) return '--'
  return TASK_LABELS[task] ?? '未知后台任务'
}
