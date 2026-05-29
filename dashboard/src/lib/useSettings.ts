import { useState, useEffect } from 'react';

export interface AppSettings {
  pageSize: number;
  dataRoot: string;     // 本地量价数据路径
  rsshubUrl: string;    // RSSHub 服务地址
  // 显示偏好
  defaultTab: 'news' | 'policy' | 'market' | 'research';  // 默认落点
  defaultMarketTab: 'realtime' | 'sentiment';  // 市场数据默认子tab
}

const DEFAULTS: AppSettings = {
  pageSize: 30,
  dataRoot: '',
  rsshubUrl: '',
  defaultTab: 'market',
  defaultMarketTab: 'realtime',
};

const KEY = 'market-radar-settings';

function load(): AppSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return DEFAULTS;
  }
}

function save(s: AppSettings) {
  localStorage.setItem(KEY, JSON.stringify(s));
}

export function useSettings() {
  const [settings, setSettings] = useState<AppSettings>(load);

  useEffect(() => {
    save(settings);
  }, [settings]);

  const update = (patch: Partial<AppSettings>) =>
    setSettings(prev => ({ ...prev, ...patch }));

  return { settings, update };
}
