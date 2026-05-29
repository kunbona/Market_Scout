// Pages filled by Agent B/C/D
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
import { useSettings } from './lib/useSettings';

import type { AppSettings } from './lib/useSettings';

const PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100];

function DisplayPrefsCard({ settings, onUpdate }: {
  settings: AppSettings;
  onUpdate: (patch: Partial<AppSettings>) => void;
}) {
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    // useSettings 已自动同步到 localStorage，这里只做视觉反馈
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
      <h3 className="text-base font-semibold text-gray-900 mb-1">显示偏好</h3>
      <p className="text-xs text-gray-400 mb-5">页面布局与默认展示方式，刷新后生效</p>
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
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  settings.defaultTab === id
                    ? 'bg-gradient-to-r from-blue-500 to-purple-600 text-white shadow-sm'
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
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                  settings.defaultMarketTab === id
                    ? 'bg-gradient-to-r from-blue-500 to-purple-600 text-white shadow-sm'
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
                className={`min-w-[40px] h-8 px-3 rounded-lg text-sm font-medium transition-all ${
                  size === settings.pageSize
                    ? 'bg-gradient-to-r from-blue-500 to-purple-600 text-white shadow-sm'
                    : 'border border-gray-200 text-gray-600 hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                {size}
              </button>
            ))}
          </div>
        </div>

      </div>

      {/* 保存按钮 */}
      <div className="flex items-center gap-3 mt-5 pt-5 border-t border-gray-100">
        <button
          onClick={handleSave}
          className="px-4 py-2 text-sm text-white bg-gradient-to-r from-blue-500 to-purple-600 rounded-lg hover:from-blue-600 hover:to-purple-700 transition-all shadow-sm"
        >
          保存偏好设置
        </button>
        {saved && (
          <span className="text-xs text-green-600">✓ 已保存，刷新后生效</span>
        )}
      </div>
    </div>
  );
}

function SettingsPage({ settings, onUpdate }: {
  settings: AppSettings;
  onUpdate: (patch: Partial<AppSettings>) => void;
}) {
  const [serverConfig, setServerConfig] = useState<{ data_root: string; rsshub_url: string; flask_port: string } | null>(null);
  const [dataRootInput, setDataRootInput] = useState('');
  const [rsshubInput, setRsshubInput] = useState('');
  const [flaskPortInput, setFlaskPortInput] = useState('');
  const [rsshubTesting, setRsshubTesting] = useState(false);
  const [rsshubTestMsg, setRsshubTestMsg] = useState('');

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

  const [savingPort, setSavingPort] = useState(false);
  const [savePortMsg, setSavePortMsg] = useState('');
  const [savingData, setSavingData] = useState(false);
  const [saveDataMsg, setSaveDataMsg] = useState('');

  const handleSavePort = async () => {
    setSavingPort(true);
    setSavePortMsg('');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flask_port: flaskPortInput }),
      });
      const json = await res.json();
      if (json.success) {
        setSavePortMsg(`✓ 已保存：${json.data.changed.join('；') || '无变化'}，重启后生效`);
        setServerConfig(prev => prev ? { ...prev, flask_port: flaskPortInput } : prev);
      } else {
        setSavePortMsg(`✗ ${json.error}`);
      }
    } catch {
      setSavePortMsg('✗ 请求失败');
    } finally {
      setSavingPort(false);
    }
  };

  const handleSaveData = async () => {
    setSavingData(true);
    setSaveDataMsg('');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data_root: dataRootInput, rsshub_url: rsshubInput }),
      });
      const json = await res.json();
      if (json.success) {
        setSaveDataMsg(`✓ 已应用：${json.data.changed.join('；') || '无变化'}`);
        setServerConfig(prev => prev ? { ...prev, data_root: dataRootInput, rsshub_url: rsshubInput } : prev);
        onUpdate({ dataRoot: dataRootInput, rsshubUrl: rsshubInput });
      } else {
        setSaveDataMsg(`✗ ${json.error}`);
      }
    } catch {
      setSaveDataMsg('✗ 请求失败，请确认服务已启动');
    } finally {
      setSavingData(false);
    }
  };

  return (
    <>
      <TabHeader title="⚙️ 系统设置" subtitle="配置显示偏好与数据路径，即时生效并自动保存" />
      <div className="space-y-6 max-w-2xl">

        {/* 显示偏好（含分页） */}
        <DisplayPrefsCard settings={settings} onUpdate={onUpdate} />

        {/* 端口配置 */}
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
          <h3 className="text-base font-semibold text-gray-900 mb-1">端口配置</h3>
          <p className="text-xs text-gray-400 mb-5">修改后需重启服务生效，通过 <code className="bg-gray-100 px-1 rounded">bash start.sh</code> 启动时自动读取</p>
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
              <p className="text-xs text-gray-400 mt-1">用户访问仪表盘使用的端口</p>
            </div>

          </div>

          <div className="flex items-center gap-3 mt-5 pt-4 border-t border-gray-100">
            <button
              onClick={handleSavePort}
              disabled={savingPort}
              className="px-4 py-2 text-sm text-white bg-gradient-to-r from-blue-500 to-purple-600 rounded-lg hover:from-blue-600 hover:to-purple-700 transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {savingPort ? '保存中...' : '保存端口配置'}
            </button>
            {savePortMsg
              ? <span className={`text-xs ${savePortMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>{savePortMsg}</span>
              : <span className="text-xs text-amber-500">⚠ 修改后需重启服务生效</span>
            }
          </div>
        </div>

        {/* 数据源配置 */}
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
          <h3 className="text-base font-semibold text-gray-900 mb-1">数据源配置</h3>
          <p className="text-xs text-gray-400 mb-5">修改后点击保存，立即对运行中的服务生效，无需重启</p>

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
              <p className="text-xs text-gray-400 mt-1">用于市场情绪计算（日线 parquet 文件所在目录）</p>
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
                  {rsshubTesting ? '测试中...' : '测试连通'}
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

          <div className="mt-5 space-y-2">
            <button
              onClick={handleSaveData}
              disabled={savingData}
              className="px-4 py-2 text-sm text-white bg-gradient-to-r from-blue-500 to-purple-600 rounded-lg hover:from-blue-600 hover:to-purple-700 transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {savingData ? '保存中...' : '保存数据源配置'}
            </button>
            {saveDataMsg && (
              <p className={`text-xs ${saveDataMsg.startsWith('✓') ? 'text-green-600' : 'text-red-500'}`}>
                {saveDataMsg}
              </p>
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

export default function App() {
  const { settings, update: updateSettings } = useSettings();
  const [activeTab, setActiveTab] = useState<TabId>(() => settings.defaultTab as TabId);
  const [marketTab, setMarketTab] = useState(() => settings.defaultMarketTab);
  const [refreshKey] = useState(0);

  const renderPage = () => {
    switch (activeTab) {
      case 'news':
        return <NewsPage defaultPageSize={settings.pageSize} />;
      case 'policy':
        return <PolicyPage defaultPageSize={settings.pageSize} />;
      case 'market':
        return (
          <>
            <TabHeader title="📈 市场数据中心" subtitle="实时行情与本地量价数据" />
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
                <ControlBar lastUpdate="本地日线数据" />
                <MarketSentimentPage key={refreshKey} />
              </div>
            )}
          </>
        );
      case 'research':
        return <ResearchPage />;
      case 'ai-analysis':
        return <div>TODO: AI Analysis Page</div>;
      case 'settings':
        return <SettingsPage settings={settings} onUpdate={updateSettings} />;
      default:
        return <div>TODO</div>;
    }
  };

  return (
    <div className="flex h-screen bg-[#f8fafc] relative overflow-hidden">
      {/* Decorative background glows */}
      <div className="pointer-events-none absolute top-0 left-1/4 w-96 h-96 bg-gradient-to-br from-blue-400/10 to-cyan-400/10 rounded-full blur-3xl animate-pulse" />
      <div className="pointer-events-none absolute bottom-0 right-1/4 w-96 h-96 bg-gradient-to-br from-purple-400/10 to-pink-400/10 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '1s' }} />
      <div className="pointer-events-none absolute top-1/2 left-1/2 w-96 h-96 bg-gradient-to-br from-orange-400/5 to-rose-400/5 rounded-full blur-3xl animate-pulse" style={{ animationDelay: '2s' }} />

      <Sidebar activeTab={activeTab} setActiveTab={(tab) => setActiveTab(tab as TabId)} />
      <main className="flex-1 overflow-y-auto relative z-10">
        <div className="p-8">
          {renderPage()}
        </div>
      </main>
    </div>
  );
}
