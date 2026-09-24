import * as React from 'react';

/**
 * 第 1 层：React ErrorBoundary。
 *
 * **这一层不可省。** React 渲染期抛出的错误会卸载整棵子树，
 * 但只有 ErrorBoundary 能拿到 `componentStack`（哪个组件的哪次渲染炸的）。
 * 全局 handler 能收到错误，却拿不到组件栈。
 *
 * ⚠️ 注意它的能力边界：**只能捕获同步渲染/生命周期错误**，
 * 捕获不到异步错误、事件回调里的错误、原生崩溃 —— 那些由其他三层负责。
 */

export interface ApmErrorBoundaryProps {
  children: React.ReactNode;
  /** 出错时展示的兜底 UI。给函数则注入 reset 能力。 */
  fallback?: React.ReactNode | ((error: Error, reset: () => void) => React.ReactNode);
  /** 捕获回调。**应在这里把崩溃记入 collector。** */
  onError?: (error: Error, componentStack: string) => void;
  /** 挂载回调，用于让 SDK 知道这一层已就绪 */
  onMount?: () => void;
}

interface State {
  error: Error | null;
}

export class ApmErrorBoundary extends React.Component<ApmErrorBoundaryProps, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidMount(): void {
    try {
      this.props.onMount?.();
    } catch {
      /* 挂载回调失败不影响渲染 */
    }
  }

  override componentDidCatch(error: Error, info: React.ErrorInfo): void {
    try {
      this.props.onError?.(error, info.componentStack ?? '');
    } catch {
      /* 上报失败绝不能导致二次崩溃 */
    }
  }

  private readonly reset = (): void => {
    this.setState({ error: null });
  };

  override render(): React.ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;

    const { fallback } = this.props;
    if (typeof fallback === 'function') return fallback(error, this.reset);
    if (fallback !== undefined) return fallback;
    // 没有兜底 UI 时渲染 null —— 宁可空白也不要二次崩溃
    return null;
  }
}
