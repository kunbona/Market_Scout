import { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { TabHeader } from './components/TabHeader';
import { FilterTabs } from './components/FilterTabs';
import { ControlBar } from './components/ControlBar';
import { NewsPage } from './pages/NewsPage';
import { PolicyPage } from './pages/PolicyPage';
import { ResearchPage } from './pages/ResearchPage';
import { MarketRealtimePage } from './pages/MarketRealtimePage';
import { MarketSentimentPage } from './pages/MarketSentimentPage';
import { AgentPage } from './pages/AgentPage';
import { useSettings } from './lib/useSettings';

import type { AppSettings } from './lib/useSettings';

const PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100];


function SettingsPage({ settings, onUpdate }: {
  settings: AppSettings;
  onUpdate: (patch: Partial<AppSettings>) => void;
}) {
  const [serverConfig, setServerConfig] = useState<{ data_root: string; rsshub_url: string; flask_port: string; quant_workers: string; agent_enabled: string; compute_enabled: string } | null>(null);
  const [agentEnabledMsg, setAgentEnabledMsg] = useState('');
  const [computeEnabledMsg, setComputeEnabledMsg] = useState('');
  const [dataRootInput, setDataRootInput] = useState('');
  const [rsshubInput, setRsshubInput] = useState('');
  const [flaskPortInput, setFlaskPortInput] = useState('');
  const [quantWorkersInput, setQuantWorkersInput] = useState('');
  const [rsshubTesting, setRsshubTesting] = useState(false);
  const [rsshubTestMsg, setRsshubTestMsg] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // 初次进入读取服务端当前值
  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(j => {
        if (j.success) {
          setServerConfig(j.data);
          setDataRootInput(j.data.data_root);
          setRsshubInput(j.data.rsshub_url);
          setFlaskPortInput(j.data.flask_port ?? '');
          setQuantWorkersInput(j.data.quant_workers ?? '');
        }
      })
      .catch(() => {});
  }, []);

  const handleTestRsshub = async () => {
    setRsshubTesting(true);
    setRsshubTestMsg('');
    try {
      const url = rsshubInput ? `?url=${encodeURIComponent(rsshubInput)}` : '';
      const res = await fetch(`/api/config/test-rsshub${url}`);
      const json = await res.json();
      if (json.success) {
        setRsshubTestMsg(json.data.ok ? `✓ ${json.data.reason}` : `✗ ${json.data.reason}`);
      }
    } catch {
      setRsshubTestMsg('✗ 请求失败');
    } finally {
      setRsshubTesting(false);
    }
  };

  const handleSaveAll = async () => {
    setSaving(true);
    setSaveMsg('');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          data_root: dataRootInput,
          rsshub_url: rsshubInput,
          flask_port: flaskPortInput,
          quant_workers: quantWorkersInput,
        }),
      });
      const json = await res.json();
      if (json.success) {
        const changed = json.data.changed ?? [];
        setSaveMsg(changed.length > 0 ? '✓ 已保存' : '✓ 配置无变化');
        setServerConfig(prev => prev ? {
          ...prev,
          data_root: dataRootInput,
          rsshub_url: rsshubInput,
          flask_port: flaskPortInput,
          quant_workers: quantWorkersInput,
        } : prev);
        onUpdate({ dataRoot: dataRootInput, rsshubUrl: rsshubInput });
      } else {
        setSaveMsg(`✗ ${json.error}`);
      }
    } catch {
      setSaveMsg('✗ 保存失败，请确认服务正在运行');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <TabHeader title="系统设置" />
      <div className="space-y-6 max-w-2xl">

        {/* 所有设置合并为一张卡片 */}
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">

          {/* ── 显示偏好 ── */}
          <p className="text-xs font-medium text-gray-500 mb-3">显示偏好</p>
          <div className="space-y-5">

            {/* 启动默认页面 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">启动默认页面</p>
                <p className="text-xs text-gray-400 mt-0.5">打开看板时默认进入哪个模块</p>
              </div>
              <div className="flex items-center gap-2">
                {[
                  { id: 'market',   label: '📈 市场数据' },
                  { id: 'news',     label: '📰 财经快讯' },
                  { id: 'policy',   label: '📋 政策动态' },
                  { id: 'research', label: '📑 研究报告' },
                ].map(({ id, label }) => (
                  <button
                    key={id}
                    onClick={() => onUpdate({ defaultTab: id as AppSettings['defaultTab'] })}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors transition-shadow ${
                      settings.defaultTab === id
                        ? 'accent-solid shadow-sm'
                        : 'border border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* 市场数据默认视图 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">市场数据默认视图</p>
                <p className="text-xs text-gray-400 mt-0.5">进入市场数据时默认显示哪个视图</p>
              </div>
              <div className="flex items-center gap-2">
                {[
                  { id: 'realtime',  label: '🔴 实时数据' },
                  { id: 'sentiment', label: '📊 历史数据' },
                ].map(({ id, label }) => (
                  <button
                    key={id}
                    onClick={() => onUpdate({ defaultMarketTab: id as AppSettings['defaultMarketTab'] })}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors transition-shadow ${
                      settings.defaultMarketTab === id
                        ? 'accent-solid shadow-sm'
                        : 'border border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* 列表每页条数 */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-700">列表每页显示条数</p>
                <p className="text-xs text-gray-400 mt-0.5">适用于财经快讯、政策动态</p>
              </div>
              <div className="flex items-center gap-2">
                {PAGE_SIZE_OPTIONS.map(size => (
                  <button
                    key={size}
                    onClick={() => onUpdate({ pageSize: size })}
                    className={`min-w-[40px] h-8 px-3 rounded-lg text-sm font-medium transition-colors transition-shadow ${
                      size === settings.pageSize
                        ? 'accent-solid shadow-sm'
                        : 'border border-gray-200 text-gray-600 hover:border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    {size}
                  </button>
                ))}
              </div>
            </div>

          </div>

          <hr className="border-gray-100 my-5" />

          {/* ── 服务配置 ── */}
          <p className="text-xs font-medium text-gray-500 mb-3">服务配置</p>
          <div className="space-y-4">
            {/* 本地量价数据路径 */}
            <div>
              <label className="block text-sm text-gray-700 mb-1.5">
                本地量价数据路径
                {serverConfig && (
                  <span className="ml-2 text-xs text-gray-400 font-normal">当前：{serverConfig.data_root}</span>
                )}
              </label>
              <input
                type="text"
                value={dataRootInput}
                onChange={e => setDataRootInput(e.target.value)}
                placeholder="/path/to/Quant_Data"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 font-mono text-gray-700"
              />
              <p className="text-xs text-gray-400 mt-1">每日收盘后计算情绪指标时读取此目录的 CSV/parquet 文件</p>
            </div>

            {/* RSSHub 地址 */}
            <div>
              <label className="block text-sm text-gray-700 mb-1.5">
                RSSHub 服务地址
                {serverConfig && (
                  <span className="ml-2 text-xs text-gray-400 font-normal">当前：{serverConfig.rsshub_url}</span>
                )}
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={rsshubInput}
                  onChange={e => { setRsshubInput(e.target.value); setRsshubTestMsg(''); }}
                  placeholder="http://localhost:1200"
                  className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 font-mono text-gray-700"
                />
                <button
                  onClick={handleTestRsshub}
                  disabled={rsshubTesting}
                  className="px-3 py-2 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 hover:border-gray-300 transition-colors disabled:opacity-50 whitespace-nowrap"
                >
                  {rsshubTesting ? '检测中...' : '检测连接'}
                </button>
              </div>
              <div className="flex items-center justify-between mt-1">
                <p className="text-xs text-gray-400">用于财联社、金十数据、格隆汇、政策 RSS 抓取</p>
                {rsshubTestMsg && (
                  <span className={`text-xs ${rsshubTestMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>
                    {rsshubTestMsg}
                  </span>
                )}
              </div>
            </div>
          </div>

          <hr className="border-gray-100 my-5" />

          {/* ── 服务参数 ── */}
          <p className="text-xs font-medium text-gray-500 mb-3">服务参数</p>
          <div className="space-y-4">
            {/* 仪表盘访问端口 FLASK_PORT */}
            <div>
              <label className="block text-sm text-gray-700 mb-1.5">
                仪表盘访问端口
                <span className="ml-1.5 text-xs font-mono text-gray-400">FLASK_PORT</span>
                {serverConfig && (
                  <span className="ml-2 text-xs text-gray-400 font-normal">当前：{serverConfig.flask_port}</span>
                )}
              </label>
              <input
                type="text"
                value={flaskPortInput}
                onChange={e => setFlaskPortInput(e.target.value)}
                placeholder="20026"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 font-mono text-gray-700"
              />
              <span className="text-xs text-amber-500 mt-1 block">⚠ 修改后需重启服务生效</span>
            </div>

            {/* 计算进程数 QUANT_WORKERS */}
            <div>
              <label className="block text-sm text-gray-700 mb-1.5">
                量价计算进程数
                <span className="ml-1.5 text-xs font-mono text-gray-400">QUANT_WORKERS</span>
                {serverConfig && (
                  <span className="ml-2 text-xs text-gray-400 font-normal">
                    当前：{serverConfig.quant_workers || '自动（CPU核数/2）'}
                  </span>
                )}
              </label>
              <input
                type="text"
                value={quantWorkersInput}
                onChange={e => setQuantWorkersInput(e.target.value)}
                placeholder="默认自动（CPU 核数 / 2）"
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 font-mono text-gray-700"
              />
              <span className="text-xs text-green-600 mt-1 block">修改后立即生效，无需重启。填 1 可切换为单进程。</span>
            </div>
          </div>

          <hr className="border-gray-100 my-5" />

          {/* ── Agent 分析 ── */}
          <p className="text-xs font-medium text-gray-500 mb-3">Agent 分析</p>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-700">Agent 定时自动分析</p>
              <p className="text-xs text-gray-400 mt-0.5">关闭后 Agent 不会按计划自动执行（手动触发仍可用）</p>
            </div>
            <div className="flex items-center gap-2">
              {agentEnabledMsg && (
                <span className={`text-xs ${agentEnabledMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>
                  {agentEnabledMsg}
                </span>
              )}
              <button
                role="switch"
                aria-checked={serverConfig?.agent_enabled === 'true'}
                onClick={async () => {
                  if (!serverConfig) return;
                  const newVal = serverConfig.agent_enabled !== 'true';
                  setServerConfig(prev => prev ? { ...prev, agent_enabled: String(newVal) } : prev);
                  setAgentEnabledMsg('');
                  try {
                    const res = await fetch('/api/config', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ agent_enabled: newVal }),
                    });
                    const json = await res.json();
                    if (json.success) {
                      setAgentEnabledMsg('✓ 已保存');
                    } else {
                      setAgentEnabledMsg(`✗ ${json.error}`);
                      setServerConfig(prev => prev ? { ...prev, agent_enabled: String(!newVal) } : prev);
                    }
                  } catch {
                    setAgentEnabledMsg('✗ 保存失败');
                    setServerConfig(prev => prev ? { ...prev, agent_enabled: String(!newVal) } : prev);
                  }
                  setTimeout(() => setAgentEnabledMsg(''), 3000);
                }}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full transition-colors duration-200 focus:outline-none ${
                  serverConfig?.agent_enabled === 'true' ? 'bg-indigo-500' : 'bg-gray-200'
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 mt-0.5 rounded-full bg-white shadow transform transition-transform duration-200 ${
                    serverConfig?.agent_enabled === 'true' ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          <div className="flex items-center justify-between mt-4">
            <div>
              <p className="text-sm text-gray-700">每日计算定时自动执行</p>
              <p className="text-xs text-gray-400 mt-0.5">关闭后每日计算不会自动触发（手动计算仍可用），重启后生效</p>
            </div>
            <div className="flex items-center gap-2">
              {computeEnabledMsg && (
                <span className={`text-xs ${computeEnabledMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>
                  {computeEnabledMsg}
                </span>
              )}
              <button
                role="switch"
                aria-checked={serverConfig?.compute_enabled === 'true'}
                onClick={async () => {
                  if (!serverConfig) return;
                  const newVal = serverConfig.compute_enabled !== 'true';
                  setServerConfig(prev => prev ? { ...prev, compute_enabled: String(newVal) } : prev);
                  setComputeEnabledMsg('');
                  try {
                    const res = await fetch('/api/config', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ compute_enabled: newVal }),
                    });
                    const json = await res.json();
                    if (json.success) {
                      setComputeEnabledMsg('✓ 已保存，重启后生效');
                    } else {
                      setComputeEnabledMsg(`✗ ${json.error}`);
                      setServerConfig(prev => prev ? { ...prev, compute_enabled: String(!newVal) } : prev);
                    }
                  } catch {
                    setComputeEnabledMsg('✗ 保存失败');
                    setServerConfig(prev => prev ? { ...prev, compute_enabled: String(!newVal) } : prev);
                  }
                  setTimeout(() => setComputeEnabledMsg(''), 3000);
                }}
                className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full transition-colors duration-200 focus:outline-none ${
                  serverConfig?.compute_enabled === 'true' ? 'bg-indigo-500' : 'bg-gray-200'
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 mt-0.5 rounded-full bg-white shadow transform transition-transform duration-200 ${
                    serverConfig?.compute_enabled === 'true' ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3 mt-5 pt-4 border-t border-gray-100">
            <button
              onClick={handleSaveAll}
              disabled={saving}
              className="accent-solid px-4 py-2 text-sm rounded-lg shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? '保存中...' : '保存配置'}
            </button>
            {saveMsg && (
              <span className={`text-xs ${saveMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>
                {saveMsg}
              </span>
            )}
          </div>
        </div>

        {/* 数据保留策略说明 */}
        <div className="bg-blue-50 border border-blue-100 rounded-2xl p-5">
          <p className="text-xs text-blue-600 font-medium mb-1">💡 数据保留策略</p>
          <p className="text-xs text-blue-500 leading-relaxed">
            财经快讯保留 <strong>7 天</strong>，政策动态长期保留，研报保留 <strong>90 天</strong>，
            情绪指标/连板链条保留 <strong>90 天</strong>。每天凌晨 2 点自动清理，数据库始终保持合理体积。
          </p>
        </div>

      </div>
    </>
  );
}

type TabId = 'news' | 'policy' | 'market' | 'research' | 'ai-analysis' | 'settings';

interface DataAlert {
  level: 'error' | 'warning';
  code: string;
  message: string;
}

export default function App() {
  const { settings, update: updateSettings } = useSettings();
  const [activeTab, setActiveTab] = useState<TabId>(() => settings.defaultTab as TabId);
  const [marketTab, setMarketTab] = useState(() => settings.defaultMarketTab);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dataAlerts, setDataAlerts] = useState<DataAlert[]>([]);
  const [dismissedAlerts, setDismissedAlerts] = useState<Set<string>>(new Set());

  useEffect(() => {
    const fetchAlerts = () => {
      fetch('/api/data-health')
        .then(r => r.json())
        .then(j => {
          if (j.success && Array.isArray(j.data?.data_alerts)) {
            setDataAlerts(j.data.data_alerts);
          }
        })
        .catch(() => {});
    };
    fetchAlerts();
    const id = setInterval(fetchAlerts, 60_000);
    return () => clearInterval(id);
  }, []);

  const renderPage = () => {
    switch (activeTab) {
      case 'news':
        return <NewsPage defaultPageSize={settings.pageSize} />;
      case 'policy':
        return <PolicyPage defaultPageSize={settings.pageSize} />;
      case 'market':
        return (
          <>
            <TabHeader title="市场数据" />
            <FilterTabs
              tabs={[
                { id: 'realtime', label: '市场实时数据', emoji: '🔴' },
                { id: 'sentiment', label: '历史静态数据', emoji: '📊' },
              ]}
              activeTab={marketTab}
              onTabChange={(id) => setMarketTab(id as 'realtime' | 'sentiment')}
            />
            {marketTab === 'realtime' && <MarketRealtimePage />}
            {marketTab === 'sentiment' && (
              <div>
                <ControlBar lastUpdate="本地日线数据" onRefresh={() => setRefreshKey(k => k + 1)} />
                <MarketSentimentPage key={refreshKey} />
              </div>
            )}
          </>
        );
      case 'research':
        return <ResearchPage />;
      case 'ai-analysis':
        return <AgentPage />;
      case 'settings':
        return <SettingsPage settings={settings} onUpdate={updateSettings} />;
    }
  };

  return (
    <div className="flex h-screen bg-[#f8fafc] relative overflow-hidden">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-white focus:text-blue-600 focus:rounded-lg focus:shadow-lg focus:text-sm focus:font-medium"
      >
        跳至主内容
      </a>
      <Sidebar activeTab={activeTab} setActiveTab={(tab) => setActiveTab(tab as TabId)} />
      <main id="main-content" className="flex-1 overflow-hidden relative z-10 flex flex-col">
        {dataAlerts.filter(a => !dismissedAlerts.has(a.code)).map(alert => (
          <div
            key={alert.code}
            className={`flex items-start gap-3 px-5 py-3 text-sm shrink-0 ${
              alert.level === 'error'
                ? 'bg-red-50 border-b border-red-200 text-red-800'
                : 'bg-amber-50 border-b border-amber-200 text-amber-800'
            }`}
          >
            <span className="mt-0.5 shrink-0">{alert.level === 'error' ? '⚠' : '!'}</span>
            <span className="flex-1">{alert.message}</span>
            <button
              onClick={() => setDismissedAlerts(prev => new Set([...prev, alert.code]))}
              className="shrink-0 opacity-50 hover:opacity-100 text-base leading-none"
            >
              ✕
            </button>
          </div>
        ))}
        <div className="flex-1 overflow-hidden">
          <div key={activeTab} className="p-8 page-enter overflow-y-auto h-full">
            {renderPage()}
          </div>
        </div>
      </main>
    </div>
  );
}
