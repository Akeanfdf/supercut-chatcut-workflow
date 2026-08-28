'use client';

import { useMemo, useState } from 'react';

type RouteId = 'quick' | 'local' | 'original';

const routes = [
  { id: 'quick' as const, index: '01', title: '快速链接模式', desc: '两个 B 站视频链接＋一段 BGM', meta: 'BILIBILI READY' },
  { id: 'local' as const, index: '02', title: '专业本地模式', desc: '本地参考片 / 素材＋可选人工标注', meta: 'FRAME ACCURATE' },
  { id: 'original' as const, index: '03', title: '原创规划模式', desc: '文案 / 主题＋素材＋可选生成镜头', meta: 'DESIGNED GRAMMAR' },
];

const pipeline = ['授权检查','素材获取','参考 / 原创语法','真实 Beat / Onset','素材覆盖','镜头蓝图','质量门一','剪辑 / 特效 / 文字','ChatCut 审阅','质量门二','可编辑终版'];
const artifacts = ['edit_brief','visual_grammar','beat_map','asset_coverage','shot_blueprint','supercut-edit-plan-v2','quality_report'];
const bars = [20,32,16,54,34,72,27,46,84,37,64,26,95,42,68,31,76,49,88,34,60,24,73,43,91,35,66,28,79,47,97,39,71,30,86,44,62,25,74,36,90,48,69,29,81,41,58,23];
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

function isBilibili(value: string) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    return ['bilibili.com','www.bilibili.com','m.bilibili.com','b23.tv','www.b23.tv'].includes(host);
  } catch { return false; }
}

export default function Home() {
  const [route, setRoute] = useState<RouteId>('quick');
  const [reference, setReference] = useState('https://www.bilibili.com/video/BV17a8CzLEgZ/');
  const [source, setSource] = useState('');
  const [music, setMusic] = useState('');
  const [script, setScript] = useState('');
  const [rights, setRights] = useState(false);
  const [notice, setNotice] = useState('');
  const [videoErrors, setVideoErrors] = useState<Record<string, boolean>>({});

  const errors = useMemo(() => {
    const result: string[] = [];
    if (route === 'quick' && reference && !isBilibili(reference)) result.push('参考片需要有效的 B 站链接');
    if (route === 'quick' && source && !isBilibili(source)) result.push('目标素材需要有效的 B 站链接');
    if (route !== 'original' && !reference) result.push('请提供参考片');
    if (route === 'original' && !script.trim()) result.push('请填写文案或主题');
    if (!source.trim()) result.push('请提供目标素材');
    if (!music) result.push('请选择 BGM');
    if (!rights) result.push('需要确认素材权利');
    return result;
  }, [route, reference, source, script, music, rights]);

  const request = useMemo(() => ({
    schema: 'supercut-workflow-trigger-v1',
    version: '1.2.0',
    trigger: route === 'original' ? '原创规划终版' : '执行超级剪辑终版工作流',
    route,
    reference: route === 'original' ? null : reference,
    source,
    music: music || null,
    rhythm_mode: 'required',
    beat_sync: 'balanced',
    music_offset: 'auto',
    max_beat_snap_ms: 150,
    brief: route === 'original' ? script : null,
    confirm_media_rights: rights,
    review_policy: 'chatcut_required_before_final',
  }), [route, reference, source, music, script, rights]);

  function downloadRequest() {
    if (errors.length) { setNotice(errors[0]); return; }
    const blob = new Blob([JSON.stringify(request, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'supercut-request.json'; anchor.click();
    URL.revokeObjectURL(url); setNotice('任务 JSON 已下载');
  }

  async function copyTrigger() {
    if (errors.length) { setNotice(errors[0]); return; }
    const text = `${request.trigger}\n\n${JSON.stringify(request, null, 2)}`;
    await navigator.clipboard.writeText(text);
    setNotice('Codex 触发词已复制');
  }

  function mediaInput(label: string, value: string, onChange: (value: string) => void, fileAccept: string, placeholder: string) {
    const useFile = route !== 'quick';
    return <div className="field"><label>{label}</label>{useFile ? <><input className="file-input" type="file" accept={fileAccept} onChange={(event) => onChange(event.target.files?.[0]?.name ?? '')}/><span className="file-name">{value || placeholder}</span></> : <input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)}/>}</div>;
  }

  return (
    <main>
      <nav className="nav shell" aria-label="主导航">
        <a className="brand" href="#top"><span className="brand-mark">SC</span><span>Supercut</span></a>
        <div className="nav-links"><a href="#workflow">工作流</a><a href="#case">案例</a><a href="#install">安装</a></div>
        <a className="github-link" href="https://github.com/Akeanfdf/supercut-chatcut-workflow" rel="noreferrer"><span>GitHub</span><b>↗</b></a>
      </nav>

      <section className="hero shell" id="top">
        <div className="eyebrow"><span /> EFFECT-AWARE EDITING × CHATCUT <i>v1.2.0</i></div>
        <h1>让参考风格、真实节拍与叙事，<br/>落进 <em>ChatCut 可编辑终版</em></h1>
        <p className="lede">从参考片或原创文案出发，把镜头语法、Beat / Onset、特效与文字变成可审阅的多轨时间线。网页只生成任务，不上传你的素材，也不在浏览器中假装完成渲染。</p>

        <div className="route-tabs" role="tablist" aria-label="工作流路线">
          {routes.map((item) => <button role="tab" aria-selected={route === item.id} key={item.id} onClick={() => { setRoute(item.id); setNotice(''); }}><span>{item.index}</span><strong>{item.title}</strong><small>{item.desc}</small><i>{item.meta}</i></button>)}
        </div>

        <div className="task-panel" aria-label="生成剪辑任务">
          {route === 'original' ? <div className="field wide-field"><label>文案 / 主题</label><input value={script} placeholder="例如：盛夏、隧道、离别与重逢" onChange={(event) => setScript(event.target.value)}/></div> : mediaInput('参考片', reference, setReference, 'video/*', route === 'quick' ? 'https://www.bilibili.com/video/BV...' : '选择本地参考片')}
          {mediaInput('目标素材', source, setSource, 'video/*', route === 'quick' ? 'https://www.bilibili.com/video/BV...' : '选择本地目标素材')}
          <div className="field music-field"><label>BGM 文件</label><input className="file-input" type="file" accept="audio/*" onChange={(event) => setMusic(event.target.files?.[0]?.name ?? '')}/><span className="file-name">{music || '选择本地音乐'}</span></div>
          <div className="task-actions"><button className="primary" type="button" onClick={downloadRequest}>下载任务 JSON <span>↓</span></button><button className="secondary" type="button" onClick={copyTrigger}>复制 Codex 触发词</button></div>
        </div>
        <div className="task-foot"><label><input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)}/><span>我确认有权使用提供的媒体</span></label><p><b>✓</b> 不读取 Cookie，不绕过登录、付费或访问控制</p><output aria-live="polite">{notice}</output></div>
      </section>

      <section className="dark-workflow" id="workflow">
        <div className="shell">
          <header className="section-heading inverse"><p>ONE WORKFLOW · THREE ROUTES</p><h2>三条入口，汇入同一套质量标准</h2><span>自动规划不等于黑盒：每一步都有可审阅产物。</span></header>
          <div className="pipeline" aria-label="完整工作流">
            {pipeline.map((step, index) => <div className="pipeline-step" key={step}><span>{String(index + 1).padStart(2,'0')}</span><i/><strong>{step}</strong>{index < pipeline.length - 1 && <b aria-hidden="true">→</b>}</div>)}
          </div>
          <div className="tracks" aria-label="ChatCut 多轨示意">
            {[
              ['SC_STORYBOARD','缺失镜头占位','0 11%,transparent 11% 15%,#6f6e68 15% 27%,transparent 27%'],
              ['SC_MAIN','70 个可编辑源镜头','0 9%,transparent 9% 10%,#5454d8 10% 24%,transparent 24% 25%,#5454d8 25% 50%,transparent 50%'],
              ['SC_VFX_PREVIEW','27 个短转场','0 20%,transparent 20% 23%,#cb7a37 23% 25%,transparent 25% 57%,#cb7a37 57% 60%,transparent 60%'],
              ['SC_TITLES','字幕与标题策略','0 42%,transparent 42% 66%,#7f80e9 66% 81%,transparent 81%'],
              ['SC_AUDIO','BGM · source +2.720s','0 100%,#4b9873 100%'],
            ].map(([name,desc,gradient]) => <div className="track" key={name}><code>{name}</code><small>{desc}</small><span style={{background:`linear-gradient(90deg,${gradient})`}}/></div>)}
          </div>
        </div>
      </section>

      <section className="io-section shell">
        <header className="section-heading"><p>VISIBLE INPUTS · AUDITABLE OUTPUTS</p><h2>从输入到产物，全部可追溯</h2></header>
        <div className="io-grid">
          <article><span className="io-number">01 / INPUT</span><h3>你只需要提供</h3><ul><li>参考片：B 站链接或本地文件</li><li>目标素材：B 站链接或本地文件</li><li>BGM：本地音频文件</li><li className="optional">可选：文案、约束、人工标注</li></ul></article>
          <article className="artifact-card"><span className="io-number">02 / ARTIFACTS</span><h3>流程自动生成</h3><div className="artifact-list">{artifacts.map((item,index)=><span key={item}><b>{String(index+1).padStart(2,'0')}</b>{item}.json</span>)}</div></article>
          <article><span className="io-number">03 / OUTPUT</span><h3>最终得到</h3><ul><li>ChatCut 多轨可编辑时间线</li><li>精确特效代理与文字轨</li><li>字幕、音乐、原声与 SFX</li><li>验收通过后的 MP4 与 Skill</li></ul></article>
        </div>
      </section>

      <section className="case-section" id="case">
        <div className="shell">
          <div className="case-kicker"><span>最简入口</span><strong>两个 B 站视频链接＋一段音乐，即可启动完整流程</strong></div>
          <header className="case-head"><div><p>REAL CASE · REFERENCE-DRIVEN</p><h2>《通往夏天的隧道，离别的出口》</h2><a href="https://www.bilibili.com/video/BV17a8CzLEgZ/" target="_blank" rel="noreferrer">参考片 BV17a8CzLEgZ ↗</a></div><p className="case-note">本案例实际采用一条 B 站参考链接＋用户合法持有的本地目标素材＋音乐。未公开完整电影、完整参考片或原始 BGM。</p></header>
          <div className="metrics">{[['4966.206 s','源素材'],['70','镜头'],['68 s','可编辑终版'],['27','短转场'],['+2.720 s','BGM 偏移'],['0.696807','音频相关度']].map(([value,label])=><div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
          <div className="case-visuals">
            <article className="wave-card"><div className="card-title"><span>RHYTHM GRAMMAR · ABSTRACT REPLAY</span><code>00:00 — 01:08</code></div><div className="waveform" aria-label="参考节奏抽象回放">{bars.map((height,index)=><i key={index} style={{height:`${height}%`}} className={index%7===5?'strong':''}/>)}</div><div className="wave-labels"><span>叙事铺垫</span><span>人物靠近</span><span>隧道推进</span><span>情绪停留</span><span>安静收束</span></div></article>
            <article className="gate-card"><div className="gate"><span>QUALITY GATE 01</span><strong>96<small>/100</small></strong><i><b style={{width:'96%'}}/></i><p>该案例按参考片 70 个切点与同源 BGM 偏移完成；v1.2 新任务另生成真实 beat_map 验收。</p></div><div className="gate"><span>QUALITY GATE 02</span><strong>97<small>/100</small></strong><i><b style={{width:'97%'}}/></i><p>结构、合成像素、音画衔接、字幕安全与完整播放通过。</p></div></article>
          </div>
          <div className="shot-map"><div className="card-title"><span>70-SHOT ABSTRACT TIMELINE</span><code>60 FPS · 4080 FRAMES</code></div><div>{Array.from({length:70},(_,index)=><i key={index} className={index>39&&index<53?'people':index>52&&index<62?'tunnel':index>61&&index<64?'hold':index>63?'outro':''}/>)}</div><footer><span>40 个盛夏微镜头</span><span>13 个人物连接</span><span>9 个隧道推进</span><span>2 个情绪长镜</span><span>6 个收束镜头</span></footer></div>
        </div>
      </section>

      <section className="demo-section shell">
        <header className="section-heading"><p>REPRODUCIBLE PROOF</p><h2>真实运行回放与案例前 20 秒</h2><span>正式媒体由 ChatCut 时间线导出；资源未就绪时页面会明确显示降级提示。</span></header>
        <div className="video-grid">
          {[
            ['codex','Codex 工作流动态回放','36 秒 · 实际日志 / 波形 / 指标',`${basePath}/media/codex-workflow-demo.mp4`],
            ['tunnel','《通往夏天的隧道》终版前 20 秒','20 秒 · 保留声音 · ChatCut 精确区间',`${basePath}/media/tunnel-case-first-20s.mp4`],
          ].map(([id,title,meta,src])=><article className="video-card" key={id}><div className="video-stage">{!videoErrors[id]&&<video controls preload="metadata" onError={()=>setVideoErrors(current=>({...current,[id]:true}))}><source src={src} type="video/mp4"/></video>}{videoErrors[id]&&<div className="video-fallback"><span>MEDIA PENDING</span><strong>等待 ChatCut 正式导出</strong><p>不会使用本地扁平视频冒充可编辑终版。</p></div>}</div><div><span>{meta}</span><h3>{title}</h3></div></article>)}
        </div>
      </section>

      <section className="install-section" id="install"><div className="shell install-grid"><div><p>INSTALL · ONE COMMAND AFTER IMPORT</p><h2>导入 Skill，配置自动下载并校验</h2><span>真实 Onset 运行时、版本化配置、38 项特效知识库、B站获取策略与 ChatCut 审阅规范一起安装。</span></div><pre><code><b>$</b> python install_workflow.py \<br/>  --source supercut-chatcut-workflow-1.2.0.zip<br/><br/><em>✓ SHA-256 verified</em><br/><em>✓ config 1.2.0 ready</em></code></pre></div></section>

      <footer className="footer shell"><div className="brand"><span className="brand-mark">SC</span><span>Supercut × ChatCut</span></div><p>Effect-aware editing that stays editable.</p><span>MIT · v1.2.0</span></footer>
    </main>
  );
}
