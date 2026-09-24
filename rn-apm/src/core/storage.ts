import type { StorageAdapter } from '../types';

/**
 * 内存存储。用于测试与"没有可用持久化层"时的降级。
 */
export class MemoryStorage implements StorageAdapter {
  private readonly map = new Map<string, string>();

  async getItem(key: string): Promise<string | null> {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }

  async setItem(key: string, value: string): Promise<void> {
    this.map.set(key, value);
  }

  async removeItem(key: string): Promise<void> {
    this.map.delete(key);
  }
}

/**
 * 把任意存储适配器包成"永不抛错"的版本。
 *
 * **埋点系统绝不能因为存储失败而影响业务。**
 * 用户没登录、磁盘满、存储被清 —— 这些都不该让 App 崩，也不该让埋点抛异常。
 * 失败时静默降级并计入 errorCount 供诊断。
 */
export class SafeStorage implements StorageAdapter {
  private errors = 0;

  constructor(private readonly inner: StorageAdapter) {}

  get errorCount(): number {
    return this.errors;
  }

  async getItem(key: string): Promise<string | null> {
    try {
      return await this.inner.getItem(key);
    } catch {
      this.errors++;
      return null;
    }
  }

  async setItem(key: string, value: string): Promise<void> {
    try {
      await this.inner.setItem(key, value);
    } catch {
      this.errors++;
    }
  }

  async removeItem(key: string): Promise<void> {
    try {
      await this.inner.removeItem(key);
    } catch {
      this.errors++;
    }
  }
}
