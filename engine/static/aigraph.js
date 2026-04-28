// ── AI 图谱 Tab5 ──────────────────────────────────────────
  var _agLoaded  = false;
  var _agGraph   = null;    // {nodes, edges, meta}
  var _agQuotes  = {};      // stockId -> {pctChg, price}
  var _agTimer   = null;
  var _agHistory = {};  // {date(sorted desc): {stockId: pct}}

  function loadAigraphTab(){
    if(_agLoaded) return;
    var wrap = document.getElementById('aigraphWrap');
    if(!wrap) return;
    _agLoaded = true;
    Promise.all([
      fetch('/api/ai-graph').then(function(r){ return r.json(); }),
      fetch('/api/ag-history').then(function(r){ return r.json(); }).catch(function(){ return {}; })
    ]).then(function(results){
      _agGraph   = results[0];
      _agHistory = results[1] || {};
      _renderAigraphPC(wrap, _agGraph);
      _agScaleMobile();
      _agStartQuoteTimer();
    }).catch(function(){ wrap.innerHTML='<div class="aigraph-loading">图谱加载失败</div>'; });
  }

  // 获取一条链路所有步骤的A股 stockId 集合
  function _agChainStockIds(chain){
    var ids = [];
    var idx = _agBuildIndex();
    if(!idx) return ids;
    (chain.steps || []).forEach(function(step){
      var l2id = step.id;
      // 图谱 stock 节点
      Object.keys(idx.stockSetByL2[l2id] || {}).forEach(function(sid){
        if(ids.indexOf(sid) < 0) ids.push(sid);
      });
      // curatedStocks A股
      var l2node = idx.nodeById[l2id];
      if(l2node){
        (l2node.curatedStocks || []).forEach(function(s){
          if(s.market === 'A' && s.code && ids.indexOf(s.code) < 0) ids.push(s.code);
        });
      }
    });
    return ids;
  }

  // 计算一组 stockId 的A股平均涨幅（从 _agQuotes 实时数据）
  function _agAvgPct(stockIds){
    var sum = 0, cnt = 0;
    stockIds.forEach(function(sid){
      var pct = _agQuotePct(_agQuotes[sid]);
      if(pct != null){ sum += pct; cnt++; }
    });
    return cnt > 0 ? sum / cnt : null;
  }

  // 计算一组 stockId 的历史平均涨幅（用于历史快照）
  function _agHistAvgPct(stockIds, dateSnap){
    var sum = 0, cnt = 0;
    stockIds.forEach(function(sid){
      var pct = dateSnap[sid];
      if(pct != null && !isNaN(pct)){ sum += pct; cnt++; }
    });
    return cnt > 0 ? sum / cnt : null;
  }

  // 计算连续上涨或连续下跌天数（包含当日）+ 历史累计涨跌幅汇总
  // 返回 {streak, cumPct, dir}，dir='up'/'dn'/''
  function _agStreakInfo(stockIds){
    var dates = Object.keys(_agHistory).sort().reverse(); // 降序，最新在前
    var streak = 0, cumPct = 0, dir = '';
    for(var i = 0; i < dates.length; i++){
      var snap = _agHistory[dates[i]];
      var avg = _agHistAvgPct(stockIds, snap);
      if(avg == null) break;
      if(i === 0){
        if(avg > 0) dir = 'up';
        else if(avg < 0) dir = 'dn';
        else break;
        streak = 1;
        cumPct = avg;
      } else {
        if(dir === 'up' && avg > 0){ streak++; cumPct += avg; }
        else if(dir === 'dn' && avg < 0){ streak++; cumPct += avg; }
        else break;
      }
    }
    return {streak: streak, cumPct: cumPct, dir: dir};
  }

  // 根据 streak 信息生成徽章 HTML（inline 累计涨幅）
  function _agStreakBadge(si, opts){
    if(!si || si.streak <= 0) return '';
    opts = opts || {};
    var cls    = _agStreakCls(si.streak, si.dir);
    var arrow  = si.dir === 'dn' ? '↓' : '↑';
    var cumStr = (si.cumPct >= 0 ? '+' : '') + si.cumPct.toFixed(1) + '%';
    var showCum = opts.showCum !== false;  // 默认显示累计涨幅
    var inner  = si.streak + '天' + arrow;
    if(showCum) inner += ' ' + cumStr;
    return '<span class="ag-streak-badge ' + cls + '">' + inner + '</span>';
  }

  // 得到连续天数对应的 CSS 颜色类
  function _agStreakCls(streak, dir){
    if(dir === 'dn'){
      if(streak >= 4) return 'ag-streak-dn-fire';
      if(streak === 3) return 'ag-streak-dn-hot';
      if(streak === 2) return 'ag-streak-dn-warm';
      return 'ag-streak-dn-cool';
    }
    if(streak >= 4) return 'ag-streak-fire';
    if(streak === 3) return 'ag-streak-hot';
    if(streak === 2) return 'ag-streak-warm';
    if(streak === 1) return 'ag-streak-cool';
    return '';
  }

  // 获取 L2 节点所有可行情的A股 stockId（stockSetByL2 + curatedStocks A股）
  function _agL2StockIds(l2id){
    var ids = [];
    var idx = _agBuildIndex();
    if(!idx) return ids;
    Object.keys(idx.stockSetByL2[l2id] || {}).forEach(function(sid){
      if(ids.indexOf(sid) < 0) ids.push(sid);
    });
    var l2node = idx.nodeById[l2id];
    if(l2node){
      (l2node.curatedStocks || []).forEach(function(s){
        if(s.market === 'A' && s.code && ids.indexOf(s.code) < 0) ids.push(s.code);
      });
    }
    return ids;
  }

  // ── 手机端导航抽屉 ──
  function _agScaleMobile(){
    var btn     = document.getElementById('agMobNavBtn');
    var overlay = document.getElementById('agMobOverlay');
    if(!btn) return;
    function openNav(){
      var nav = document.querySelector('#aigraphWrap .ag-nav');
      if(nav){ nav.classList.add('mob-open'); }
      overlay.classList.add('show');
    }
    function closeNav(){
      var nav = document.querySelector('#aigraphWrap .ag-nav');
      if(nav){ nav.classList.remove('mob-open'); }
      overlay.classList.remove('show');
    }
    btn.addEventListener('click', function(e){ e.stopPropagation(); openNav(); });
    overlay.addEventListener('click', closeNav);
    document.querySelectorAll('#aigraphWrap .ag-nav-item, #aigraphWrap .ag-nav-overview').forEach(function(el){
      el.addEventListener('click', function(){ if(window.innerWidth <= 600) closeNav(); });
    });
  }

  // ── 行情加载（服务端每20分钟缓存，前端只取一次）──
  function _agStartQuoteTimer(){
    if(_agTimer) return;
    _agTimer = 1; // 标记已启动，防止重复
    _agFetchQuotes();
  }
  function _agFetchQuotes(){
    if(!_agGraph) return;
    fetch('/api/ai-quotes')
      .then(function(r){ return r.json(); })
      .then(function(qt){
        _agQuotes = qt || {};
        _agApplyHotness();
      })
      .catch(function(){});
  }

  function _agPctNum(v){
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function _agQuotePct(q){
    if(q == null) return null;
    if(Array.isArray(q)) return _agPctNum(q[0]);
    if(typeof q === 'object') return _agPctNum(q.pctChg);
    return _agPctNum(q);
  }

  function _agFmtPct(v){
    var n = _agPctNum(v);
    if(n == null) return '—';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function _agPctCls(v){
    var n = _agPctNum(v);
    if(n == null) return 'flat';
    return n > 0 ? 'up' : n < 0 ? 'dn' : 'flat';
  }

  function _agBuildIndex(){
    if(!_agGraph) return null;
    var g = _agGraph;
    var nodeById = {};
    var subjectsByL2 = {};
    var subjectStocks = {};
    var stockSetByL2 = {};

    (g.nodes||[]).forEach(function(n){ nodeById[n.id] = n; });

    (g.nodes||[]).forEach(function(n){
      if(n.type !== 'subject') return;
      var l2id = 'l2::'+n.l1+'::'+n.l2;
      if(!subjectsByL2[l2id]) subjectsByL2[l2id] = [];
      subjectsByL2[l2id].push(n);
    });

    (g.edges||[]).forEach(function(e){
      if(e.type !== 'includes') return;
      var src = (typeof e.source === 'object') ? e.source.id : e.source;
      var tgt = (typeof e.target === 'object') ? e.target.id : e.target;
      if(!src || !tgt) return;
      var subj = nodeById[src];
      if(!subj || subj.type !== 'subject') return;
      var stockId = String(tgt).replace('stock::', '');
      if(!stockId || stockId === tgt) return;
      if(!subjectStocks[src]) subjectStocks[src] = [];
      subjectStocks[src].push(stockId);
      var l2id = 'l2::'+subj.l1+'::'+subj.l2;
      if(!stockSetByL2[l2id]) stockSetByL2[l2id] = {};
      stockSetByL2[l2id][stockId] = 1;
    });

    Object.keys(subjectsByL2).forEach(function(l2id){
      subjectsByL2[l2id].sort(function(a,b){
        return (_agPctNum(b.pctChg)||0) - (_agPctNum(a.pctChg)||0);
      });
    });

    return {
      nodeById: nodeById,
      subjectsByL2: subjectsByL2,
      subjectStocks: subjectStocks,
      stockSetByL2: stockSetByL2,
    };
  }

  function _agSubjectPct(subj, stockIds){
    var ids = stockIds || [];
    if(ids.length){
      var sum = 0, cnt = 0;
      ids.forEach(function(sid){
        var pct = _agQuotePct(_agQuotes[sid]);
        if(pct == null) return;
        sum += pct;
        cnt += 1;
      });
      if(cnt > 0) return sum / cnt;
    }
    var fallback = _agPctNum(subj.pctChg);
    return fallback == null ? 0 : fallback;
  }

  // ── 热点强度计算（L2 级别）──
  function _agCalcHotness(){
    var l2heat = {};  // l2_id -> {totalPct, count, maxPct, hotCount}
    if(!_agGraph) return l2heat;
    var idx = _agBuildIndex();
    if(!idx) return l2heat;
    Object.keys(idx.subjectsByL2).forEach(function(l2id){
      var box = {totalPct:0, count:0, maxPct:-999, hotCount:0};
      (idx.subjectsByL2[l2id] || []).forEach(function(subj){
        var pct = _agSubjectPct(subj, idx.subjectStocks[subj.id]);
        box.totalPct += pct;
        box.count += 1;
        if(pct > box.maxPct) box.maxPct = pct;
        if(pct > 3) box.hotCount += 1;
      });
      if(box.count === 0) box.maxPct = 0;
      l2heat[l2id] = box;
    });
    return l2heat;
  }

  function _agHotLabel(pct, maxPct){
    if(pct > 5 || maxPct > 8)  return '⚡';
    if(pct > 3 || maxPct > 5)  return '🔥';
    if(pct > 1.5)               return '↗';
    return '';
  }

  // ── 股票行渲染配置模板（统一入口，改此处即全局生效）──
  // 参数：name, code, pct(number|null), options{sid, dot, isOverseas, overseaMarket}
  function _agStockRowHtml(name, code, pct, opts){
    opts = opts || {};
    var sid       = opts.sid || '';
    var dot       = opts.dot || '';          // 彩色小圆点颜色，空则不显示
    var isOverseas = opts.isOverseas || false;
    var pctCls    = pct == null ? 'flat' : (pct > 0 ? 'up' : pct < 0 ? 'dn' : 'flat');
    var pctStr    = pct == null ? '\u2014' : ((pct >= 0 ? '+' : '') + pct.toFixed(2) + '%');
    var pctHtml   = isOverseas
      ? '<span class="ag-stock-pct flat">\u2014</span>'
      : '<span class="ag-stock-pct pct-val ' + pctCls + '">' + pctStr + '</span>';
    var dotHtml   = dot
      ? '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:' + dot + ';margin-right:4px;vertical-align:middle;flex-shrink:0"></span>'
      : '';
    var sidAttr   = sid ? ' data-sid="' + sid + '"' : '';
    return '<div class="ag-stock-row"' + sidAttr + '>'
      + '<span class="ag-stock-name">' + dotHtml + name + '</span>'
      + '<span class="ag-stock-code">' + code + '</span>'
      + pctHtml
      + '</div>';
  }

  // 全景架构图股票行（小尺寸版）
  function _agArchStockRowHtml(name, code, pct, opts){
    opts = opts || {};
    var sid        = opts.sid || '';
    var dot        = opts.dot || '';
    var isOverseas = opts.isOverseas || false;
    var pctCls     = pct == null ? 'flat' : (pct > 0 ? 'up' : pct < 0 ? 'dn' : 'flat');
    var pctStr     = pct == null ? '\u2014' : ((pct >= 0 ? '+' : '') + pct.toFixed(2) + '%');
    var pctHtml    = isOverseas
      ? '<span class="ag-arch-spct flat">\u2014</span>'
      : '<span class="ag-arch-spct pct-val ' + pctCls + '">' + pctStr + '</span>';
    var dotHtml    = dot
      ? '<span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:' + dot + ';margin-right:3px;vertical-align:middle"></span>'
      : '';
    var sidAttr    = sid ? ' data-sid="' + sid + '"' : '';
    return '<div class="ag-arch-stock"' + sidAttr + '>'
      + '<span class="ag-arch-sname">' + dotHtml + name + '</span>'
      + '<span class="ag-arch-smkt" style="font-size:10px;color:#bbb">' + code + '</span>'
      + pctHtml
      + '</div>';
  }

  // ── PC 清晰链路视图（固定排布，无拖拽）──
  function _renderAigraphPC(wrap, g){
    wrap.classList.remove('aigraph-tree');
    var idx = _agBuildIndex();
    var l2heat = _agCalcHotness();
    var chains = (g.meta && g.meta.logicChains) || [];

    // ── 按第一个步骤的 L1 对链路分组 ──
    var L1_ORDER = ['算力芯片','算力基建','元器件/材料','大模型/软件','AI应用','端侧AI','具身智能/机器人','AI能源'];
    var L1_COLOR = {'算力芯片':'#e53935','算力基建':'#e64a19','元器件/材料':'#f57c00',
      '大模型/软件':'#7b1fa2','AI应用':'#1565c0','端侧AI':'#00838f',
      '具身智能/机器人':'#2e7d32','AI能源':'#558b2f'};
    var groupMap = {};
    L1_ORDER.forEach(function(l1){ groupMap[l1] = []; });
    chains.forEach(function(c, ci){
      var firstL1 = (c.steps && c.steps[0]) ? c.steps[0].l1 : '其他';
      if(!groupMap[firstL1]) groupMap[firstL1] = [];
      groupMap[firstL1].push({c: c, ci: ci});
    });

    // ── 左侧导航栏 ──
    var navHtml = '<div class="ag-nav" id="agNav">';
    navHtml += '<div class="ag-nav-hd">产业链导航<span class="ag-nav-hd-cnt">'+chains.length+'</span></div>';

    var _grpIdx = 0;
    L1_ORDER.forEach(function(l1){
      var grp = groupMap[l1];
      if(!grp || !grp.length) return;
      var color = L1_COLOR[l1] || '#888';
      var gid = 'agNavGrp'+(_grpIdx++);
      navHtml += '<div class="ag-nav-group">';
      navHtml += '<div class="ag-nav-group-hd" data-gid="'+gid+'">';
      navHtml += '<div class="ag-nav-group-dot" style="background:'+color+'"></div>';
      navHtml += '<div class="ag-nav-group-name">'+l1+'</div>';
      navHtml += '<div class="ag-nav-group-cnt">'+grp.length+'</div>';
      navHtml += '<div class="ag-nav-group-arrow">▶</div>';
      navHtml += '</div>';
      navHtml += '<div class="ag-nav-group-body" id="'+gid+'" style="display:block">';
      grp.forEach(function(item, itemIdx){
        var chainId = 'ag-chain-' + item.ci;
        var label = item.c.name.replace(/[（(].*/,'').trim();
        var sids = _agChainStockIds(item.c);
        var avg  = _agAvgPct(sids);
        var si   = _agStreakInfo(sids);
        var avgStr  = avg != null ? ((avg>=0?'+':'')+avg.toFixed(2)+'%') : '';
        var avgCls  = avg == null ? '' : (avg > 0 ? 'up' : avg < 0 ? 'dn' : 'flat');
        var strBadge = _agStreakBadge(si);
        navHtml += '<div class="ag-nav-item" data-anchor="'+chainId+'" data-chainidx="'+item.ci+'">';
        navHtml += '<div class="ag-nav-item-main">';
        navHtml += '<span class="ag-nav-item-num">'+(itemIdx+1)+'.</span>';
        navHtml += '<span class="ag-nav-item-label">'+label+'</span>';
        navHtml += '</div>';
        navHtml += '<div class="ag-nav-item-meta">';
        navHtml += '<span class="ag-nav-pct pct-val '+avgCls+'" data-navpct="'+item.ci+'" style="'+(avgStr?'':'display:none')+'">'+avgStr+'</span>';
        navHtml += '<span data-navstreak="'+item.ci+'">' + strBadge + '</span>';
        navHtml += '</div>';
        navHtml += '</div>';
      });
      navHtml += '</div></div>';
    });

    navHtml += '<div class="ag-nav-overview" data-anchor="ag-overview">🗺 全景架构图</div>';
    navHtml += '</div>';

    // ── 右侧内容区 ──
    var html = navHtml + '<div class="ag-content" id="agContent">';
    html += '<div class="ag-board">';

    if(!chains.length){
      html += '<div class="ag-chain"><div class="ag-chain-title">未配置逻辑链</div></div>';
    }

    chains.forEach(function(c, ci){
      var chainId  = 'ag-chain-' + ci;
      var csids    = _agChainStockIds(c);
      var cavg     = _agAvgPct(csids);
      var cavgStr  = cavg != null ? ((cavg>=0?'+':'')+cavg.toFixed(2)+'%') : '';
      var cavgCls  = cavg == null ? '' : (cavg > 0 ? 'up' : cavg < 0 ? 'dn' : 'flat');
      var csi      = _agStreakInfo(csids);
      var cstrBadge= _agStreakBadge(csi);
      html += '<div class="ag-chain" id="'+chainId+'">';
      html += '<div class="ag-chain-title-row">';
      html += '<div class="ag-chain-title">'+c.name+'</div>';
      html += '<div class="ag-chain-title-meta">';
      html += '<span class="ag-chain-avg pct-val '+cavgCls+'" data-chainpct="'+ci+'" style="'+(cavgStr?'':'display:none')+'">A股均 '+cavgStr+'</span>';
      html += '<span data-chainstreak="'+ci+'">' + cstrBadge + '</span>';
      html += '</div>';
      html += '</div>';
      html += '<div class="ag-chain-row">';

      (c.steps || []).forEach(function(step, i){
        var l2id = step.id;
        var l2node = (idx && idx.nodeById[l2id]) ? idx.nodeById[l2id] : {name: step.l2};
        var heat = l2heat[l2id] || {totalPct:0, count:0, maxPct:0};
        var avgPct = heat.count ? (heat.totalPct / heat.count) : null;
        var allSubj = (idx && idx.subjectsByL2[l2id]) ? idx.subjectsByL2[l2id] : [];
        var sortedSubj = allSubj.slice().sort(function(a, b){
          var bStocks = idx && idx.subjectStocks ? idx.subjectStocks[b.id] : null;
          var aStocks = idx && idx.subjectStocks ? idx.subjectStocks[a.id] : null;
          return _agSubjectPct(b, bStocks) - _agSubjectPct(a, aStocks);
        });
        var topSubj = sortedSubj.slice(0, 5);
        var stockRank = [];
        if(idx && idx.stockSetByL2 && idx.stockSetByL2[l2id]){
          Object.keys(idx.stockSetByL2[l2id]).forEach(function(sid){
            var sn = idx.nodeById['stock::'+sid] || {};
            var sp = _agQuotePct(_agQuotes[sid]);
            if(sp == null) sp = _agPctNum(sn.pctChg);
            stockRank.push({id: sid, name: sn.name || sid, pct: sp, market:'A'});
          });
          stockRank.sort(function(a, b){
            return (b.pct == null ? -999 : b.pct) - (a.pct == null ? -999 : a.pct);
          });
        }
        var topStocks = stockRank.slice(0, 8);
        var curatedStocks = l2node.curatedStocks || [];
        var hot = _agHotLabel(avgPct || 0, heat.maxPct || 0);

        html += '<div class="ag-step" data-l2id="'+l2id+'">';
        html += '<div class="ag-step-hd">';
        html += '<div class="ag-step-name">'+(l2node.name || step.l2)+(hot ? (' '+hot) : '')+'</div>';
        html += '<span class="ag-step-l1">'+step.l1+'</span>';
        html += '</div>';
        var stepSids = _agL2StockIds(l2id);
        var stepAvg  = _agAvgPct(stepSids);
        var stepSi   = _agStreakInfo(stepSids);
        var stepAvgStr = stepAvg != null ? (stepAvg>=0?'+':'')+stepAvg.toFixed(2)+'%' : '';
        var stepAvgCls = stepAvg == null ? 'flat' : (stepAvg > 0 ? 'up' : stepAvg < 0 ? 'dn' : 'flat');
        html += '<div class="ag-step-metrics" data-l2id-metrics="'+l2id+'">';
        html += '<span>题材 '+allSubj.length+'</span>';
        html += '<span class="ag-step-aavg pct-val '+stepAvgCls+'" data-stepaavg="'+l2id+'" style="'+(stepAvgStr?'':'display:none')+'">'+stepAvgStr+'</span>';
        if(stepSi.streak > 0) html += _agStreakBadge(stepSi, {showCum: false});
        html += '</div>';

        // 题材层
        html += '<div class="ag-step-layer">';
        html += '<div class="ag-layer-title">题材层</div>';
        html += '<div class="ag-layer-list">';
        if(topSubj.length){
          topSubj.forEach(function(s){
            var sStocks = idx && idx.subjectStocks ? idx.subjectStocks[s.id] : null;
            var spct = _agSubjectPct(s, sStocks);
            html += '<div class="ag-subj-pill" data-subjid="'+s.id+'">';
            html += '<span class="ag-subj-pill-name">'+s.name+'</span>';
            html += '<span class="ag-subj-pill-pct '+_agPctCls(spct)+'" data-subjpct="'+s.id+'">'+_agFmtPct(spct)+'</span>';
            html += '</div>';
          });
        } else {
          html += '<div class="ag-layer-empty">暂无题材映射</div>';
        }
        html += '</div></div>';

        // 股票层：动态股票优先，无则显示精选（A股走QMT，海外显示静态标签）
        html += '<div class="ag-step-layer">';
        if(topStocks.length){
          html += '<div class="ag-layer-title">股票层（实时）</div>';
          html += '<div class="ag-stock-list">';
          topStocks.forEach(function(st){
            html += _agStockRowHtml(st.name, st.id, st.pct, {sid: st.id});
          });
          html += '</div>';
        } else if(curatedStocks.length){
          html += '<div class="ag-layer-title">精选龙头</div>';
          html += '<div class="ag-stock-list">';
          curatedStocks.slice(0, 8).forEach(function(st){
            var mkDot = st.market==='A' ? '#2e7d32' : (st.market==='US' ? '#1565c0' : '#e65100');
            html += _agStockRowHtml(st.name, st.code, null, {
              sid: st.market==='A' ? st.code : '',
              dot: mkDot,
              isOverseas: st.market !== 'A'
            });
          });
          html += '</div>';
        } else {
          html += '<div class="ag-layer-title">股票层</div>';
          html += '<div class="ag-stock-list"><div class="ag-layer-empty">暂无股票映射</div></div>';
        }
        html += '</div>';
        html += '</div>';

        if(i < (c.steps || []).length - 1){
          html += '<div class="ag-chain-arrow">→</div>';
        }
      });

      html += '</div></div>';
    });

    // ── 全景架构图（替代原来的赛道总览）──
    html += '<div class="ag-overview" id="ag-overview">';
    html += '<div class="ag-overview-hd">全景架构图 <span class="ag-overview-hd-sub">AI产业链 · L1→L2→龙头股</span></div>';
    html += '<div class="ag-arch">';

    L1_ORDER.forEach(function(l1){
      var l1node = (g.nodes||[]).find(function(n){ return n.type==='l1' && n.name===l1; });
      if(!l1node) return;
      var l2s = (g.nodes||[]).filter(function(n){ return n.type==='l2' && n.parent_l1===l1; });
      var color = L1_COLOR[l1] || '#888';

      html += '<div class="ag-arch-col">';
      html += '<div class="ag-arch-l1hd" style="background:'+color+'">';
      html += l1;
      html += '<span class="ag-arch-l1hd-cnt">'+(l1node.subjectCount||0)+'题材</span>';
      html += '</div>';

      l2s.forEach(function(l2){
        var heat = l2heat[l2.id] || {totalPct:0, count:0};
        var avgPct = heat.count ? (heat.totalPct / heat.count) : null;
        var curated = l2.curatedStocks || [];

        html += '<div class="ag-arch-l2card" data-l2id="'+l2.id+'">';
        html += '<div class="ag-arch-l2name">';
        html += '<span>'+l2.name+'</span>';
        html += '<span class="ag-arch-l2pct '+_agPctCls(avgPct)+'">'+_agFmtPct(avgPct)+'</span>';
        html += '</div>';
        html += '<div class="ag-arch-stocks">';

        // 显示精选龙头：A股先显示带行情
        var shown = curated.slice(0, 5);
        if(shown.length){
          shown.forEach(function(st){
            var mkDot = st.market==='A' ? '#2e7d32' : (st.market==='US' ? '#1565c0' : '#e65100');
            html += _agArchStockRowHtml(st.name, st.code, null, {
              sid: st.market==='A' ? st.code : '',
              dot: mkDot,
              isOverseas: st.market !== 'A'
            });
          });
        } else {
          html += '<div style="font-size:11px;color:#cbd5e1">暂无精选</div>';
        }
        html += '</div></div>';
      });

      html += '</div>';
    });

    html += '</div></div></div></div>';
    html += '<button class="ag-mob-nav-btn" id="agMobNavBtn">&#9776;</button>';
    html += '<div class="ag-mob-overlay" id="agMobOverlay"></div>';
    wrap.innerHTML = html;
    _agInitNavEvents();
  }

  function _agNavTo(anchorId, activeEl){
    var content = document.getElementById('agContent');
    var target = document.getElementById(anchorId);
    if(content && target){
      content.scrollTo({top: target.offsetTop - 8, behavior: 'smooth'});
    }
    document.querySelectorAll('#agNav .ag-nav-item, #agNav .ag-nav-overview').forEach(function(it){
      it.classList.remove('active');
    });
    if(activeEl) activeEl.classList.add('active');
  }

  function _agFindAncestor(el, cls){
    while(el && el !== document.body){
      if(el.classList && el.classList.contains(cls)) return el;
      el = el.parentElement;
    }
    return null;
  }

  function _agInitNavEvents(){
    var nav = document.getElementById('agNav');
    if(!nav) return;
    // 初始化：所有分组默认展开，箭头朝下
    nav.querySelectorAll('.ag-nav-group-hd').forEach(function(hd){
      var arrow = hd.querySelector('.ag-nav-group-arrow');
      if(arrow) arrow.classList.add('open');
    });
    nav.addEventListener('click', function(e){
      var t = e.target;
      // 分组折叠/展开
      var hd = _agFindAncestor(t, 'ag-nav-group-hd') || (t.classList && t.classList.contains('ag-nav-group-hd') ? t : null);
      if(hd){
        var gid = hd.getAttribute('data-gid');
        var body = document.getElementById(gid);
        var arrow = hd.querySelector('.ag-nav-group-arrow');
        if(body){
          var open = body.style.display !== 'none' && body.style.display !== '';
          body.style.display = open ? 'none' : 'block';
          if(arrow) arrow.classList.toggle('open', !open);
        }
        return;
      }
      // 链路跳转
      var item = _agFindAncestor(t, 'ag-nav-item') || (t.classList && t.classList.contains('ag-nav-item') ? t : null);
      if(item && item.getAttribute('data-anchor')){
        _agNavTo(item.getAttribute('data-anchor'), item);
        if(window.innerWidth <= 600){
          var agNav = document.querySelector('#aigraphWrap .ag-nav');
          var ov2   = document.getElementById('agMobOverlay');
          if(agNav) agNav.classList.remove('mob-open');
          if(ov2)   ov2.classList.remove('show');
        }
        return;
      }
      // 总览跳转
      var ov = _agFindAncestor(t, 'ag-nav-overview') || (t.classList && t.classList.contains('ag-nav-overview') ? t : null);
      if(ov && ov.getAttribute('data-anchor')){
        _agNavTo(ov.getAttribute('data-anchor'), ov);
        return;
      }
    });
  }

  function _agApplyHotness(){
    if(!_agGraph) return;
    // ── 原地更新行情数值，不重建 DOM（避免回顶、重绘慢）──
    var idx = _agBuildIndex();
    // 1. 更新所有 data-sid 节点的涨跌幅
    applyQuotes(_agQuotes);
    // 2. 更新 ag-step-hot（L2 平均涨跌幅标签）
    var l2heat = _agCalcHotness();
    document.querySelectorAll('[data-l2id]').forEach(function(el){
      var l2id = el.dataset.l2id;
      var heat = l2heat[l2id] || {totalPct:0,count:0};
      var avgPct = heat.count ? (heat.totalPct/heat.count) : null;
      var hotEl = el.querySelector('.ag-step-hot');
      if(hotEl){
        hotEl.textContent = _agFmtPct(avgPct);
        hotEl.className = 'ag-step-hot '+_agPctCls(avgPct);
      }
      var archPctEl = el.querySelector('.ag-arch-l2pct');
      if(archPctEl){
        archPctEl.textContent = _agFmtPct(avgPct);
        archPctEl.className = 'ag-arch-l2pct '+_agPctCls(avgPct);
      }
    });
    // 3. 更新导航栏每条链路的A股平均涨幅
    var chains = (_agGraph.meta && _agGraph.meta.logicChains) || [];
    document.querySelectorAll('[data-navpct]').forEach(function(el){
      var ci = parseInt(el.getAttribute('data-navpct'));
      var c  = chains[ci];
      if(!c) return;
      var sids = _agChainStockIds(c);
      var avg  = _agAvgPct(sids);
      if(avg == null){ el.style.display = 'none'; return; }
      el.style.display = '';
      el.textContent = (avg>=0?'+':'')+avg.toFixed(2)+'%';
      el.className   = 'ag-nav-pct pct-val ' + (avg>0?'up':avg<0?'dn':'flat');
    });
    // 4. 更新链路标题旁的A股平均涨幅
    document.querySelectorAll('[data-chainpct]').forEach(function(el){
      var ci = parseInt(el.getAttribute('data-chainpct'));
      var c  = chains[ci];
      if(!c) return;
      var sids = _agChainStockIds(c);
      var avg  = _agAvgPct(sids);
      if(avg == null){ el.style.display = 'none'; return; }
      el.style.display = '';
      el.textContent = 'A股均 '+(avg>=0?'+':'')+avg.toFixed(2)+'%';
      el.className   = 'ag-chain-avg pct-val ' + (avg>0?'up':avg<0?'dn':'flat');
    });
    // 5. 更新 L2 模块（ag-step）的A股平均涨幅徽章
    document.querySelectorAll('[data-stepaavg]').forEach(function(el){
      var l2id = el.getAttribute('data-stepaavg');
      var sids = _agL2StockIds(l2id);
      var avg  = _agAvgPct(sids);
      if(avg == null){ el.style.display = 'none'; return; }
      el.style.display = '';
      el.textContent = (avg>=0?'+':'')+avg.toFixed(2)+'%';
      el.className   = 'ag-step-aavg pct-val ' + (avg>0?'up':avg<0?'dn':'flat');
    });
    // 6. 更新题材层每个 pill 的涨幅
    var idx2 = _agBuildIndex();
    document.querySelectorAll('[data-subjpct]').forEach(function(el){
      var subjId = el.getAttribute('data-subjpct');
      if(!idx2) return;
      var subj   = idx2.nodeById[subjId];
      var sStocks= idx2.subjectStocks[subjId];
      if(!subj) return;
      var spct = _agSubjectPct(subj, sStocks);
      el.textContent = _agFmtPct(spct);
      el.className   = 'ag-subj-pill-pct ' + _agPctCls(spct);
    });
  }

  // ── 移动端折叠树 ──
  function _renderAigraphMobile(wrap, g){
    if(!wrap.classList.contains('aigraph-tree')) wrap.classList.add('aigraph-tree');
    var l2heat = _agCalcHotness();
    var idx = _agBuildIndex();

    // 按 L1→L2→subject 组织
    var l1map = {};
    g.nodes.forEach(function(n){
      if(n.type==='l1') l1map[n.id] = {node:n, l2s:{}};
    });
    g.nodes.forEach(function(n){
      if(n.type==='l2'){
        var l1id = 'l1::'+n.parent_l1;
        if(l1map[l1id]) l1map[l1id].l2s[n.id] = {node:n, subjects:[]};
      }
    });
    g.nodes.forEach(function(n){
      if(n.type==='subject'){
        var l2id = 'l2::'+n.l1+'::'+n.l2;
        var l1id = 'l1::'+n.l1;
        if(l1map[l1id] && l1map[l1id].l2s[l2id]){
          l1map[l1id].l2s[l2id].subjects.push(n);
        }
      }
    });

    var html = '';
    Object.values(l1map).forEach(function(l1obj){
      var l1 = l1obj.node;
      var totalSubj = l1.subjectCount || 0;
      html += '<div class="ag-l1">';
      html += '<div class="ag-l1-hd" onclick="_agToggleL1(this)">';
      html += '<div class="ag-l1-dot" style="background:'+l1.color+'"></div>';
      html += '<div class="ag-l1-name">'+l1.name+'</div>';
      html += '<div class="ag-l1-cnt">'+totalSubj+'</div>';
      html += '<div class="ag-l1-arrow">▶</div>';
      html += '</div>';
      html += '<div class="ag-l1-body">';
      Object.values(l1obj.l2s).forEach(function(l2obj){
        var l2 = l2obj.node;
        var heat = l2heat[l2.id] || {};
        var avgPct = heat.count ? (heat.totalPct/heat.count) : 0;
        var hot = _agHotLabel(avgPct, heat.maxPct||0);
        var subjList = l2obj.subjects.slice().sort(function(a,b){
          var bStocks = idx && idx.subjectStocks ? idx.subjectStocks[b.id] : null;
          var aStocks = idx && idx.subjectStocks ? idx.subjectStocks[a.id] : null;
          return _agSubjectPct(b, bStocks) - _agSubjectPct(a, aStocks);
        });
        if(!subjList.length) return;
        html += '<div class="ag-l2">';
        html += '<div class="ag-l2-hd" onclick="_agToggleL2(this)">';
        html += '<div class="ag-l2-name">'+l2.name+(hot?' '+hot:'')+'</div>';
        html += '<div class="ag-l2-cnt">'+subjList.length+'题材</div>';
        html += '</div>';
        html += '<div class="ag-l2-body">';
        subjList.forEach(function(s){
          var sStocks = idx && idx.subjectStocks ? idx.subjectStocks[s.id] : null;
          var pct = _agSubjectPct(s, sStocks);
          var ps  = pct!=null ? ((pct>=0?'+':'')+pct.toFixed(2)+'%') : '';
          var pcls = pct>0?'up':pct<0?'dn':'flat';
          var hot2 = pct!=null ? _agHotLabel(pct, pct) : '';
          html += '<div class="ag-subj">';
          html += '<div class="ag-subj-name">'+s.name+'</div>';
          if(ps) html += '<div class="ag-subj-pct '+pcls+'">'+ps+'</div>';
          if(hot2) html += '<div class="ag-subj-hot">'+hot2+'</div>';
          html += '</div>';
        });
        html += '</div></div>';
      });
      html += '</div></div>';
    });
    wrap.innerHTML = html;
  }

  function _agToggleL1(hd){
    var body  = hd.nextElementSibling;
    var arrow = hd.querySelector('.ag-l1-arrow');
    var open  = body.style.display === 'block';
    body.style.display  = open ? 'none' : 'block';
    arrow.classList.toggle('open', !open);
  }
  function _agToggleL2(hd){
    var body = hd.nextElementSibling;
    body.style.display = body.style.display==='block' ? 'none' : 'block';
  }

  // ── 新题材 Tab（左列+长按详情+右侧子树）────────────────
  var _newLoaded = false;
  var _newChartCache = {};

  function loadNewSubjects(){
    if(_newLoaded) return;
    var listEl  = document.getElementById('newList');
    var chartEl = document.getElementById('newChart');
    if(!listEl || !chartEl) return;
    _newLoaded = true;
    fetch('/api/subject-tree')
      .then(function(r){ return r.json(); })
      .then(function(days){
        if(!days || !days.length){ listEl.innerHTML='<div class="tree-loading">暂无数据</div>'; return; }
        // 只取「新题材」类型
        var subjects = [];
        days.forEach(function(day){
          (day.children||[]).forEach(function(typeNode){
            if(typeNode.name === '新题材'){
              (typeNode.children||[]).forEach(function(subj){ subjects.push(subj); });
            }
          });
        });
        if(!subjects.length){ listEl.innerHTML='<div class="tree-loading">暂无新题材</div>'; return; }
        renderSubjectList(listEl, chartEl, subjects, _newChartCache);
      })
      .catch(function(){ listEl.innerHTML='<div class="tree-loading">加载失败</div>'; });
  }

  // ── 题材图谱 Tab ──────────────────────────────────
  var _treeListLoaded = false;
  var _treeChartCache = {};

  // 公共：渲染左列题材列表，点击切换右侧子树，长按弹出详情
  function renderSubjectList(listEl, chartEl, subjects, cache){
    var html = '';
    subjects.forEach(function(s, i){
      var p    = s.pctChg;
      var ps   = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '';
      var pcls = p < 0 ? ' dn' : '';
      html += '<div class="tree-list-item" data-idx="'+i+'" data-subid="'+(s.subjectId||'')+'">';
      html += '<div class="tli-name">'+s.name+'</div>';
      if(ps) html += '<div class="tli-pct'+pcls+'">'+ps+'</div>';
      var dateStr = s.rankDate ? s.rankDate.slice(0,10) : '';
      var timeStr = s.timeStr || '';
      if(dateStr) html += '<div class="tli-date">'+dateStr+(timeStr?' '+timeStr:'')+'</div>';
      html += '<div class="tli-hint">长按详情</div>';
      html += '</div>';
    });
    listEl.innerHTML = html;

    // 默认选中第一个
    var firstItem = listEl.querySelector('.tree-list-item');
    if(firstItem) selectSubjectWith(firstItem, subjects, chartEl, cache);

    // 点击 → 切换子树
    listEl.addEventListener('click', function(e){
      var item = e.target.closest('.tree-list-item');
      if(!item || item._longPressed) return;
      listEl.querySelectorAll('.tree-list-item').forEach(function(el){ el.classList.remove('active'); });
      item.classList.add('active');
      selectSubjectWith(item, subjects, chartEl, cache);
    });

    // 长按 → 弹出详情
    listEl.querySelectorAll('.tree-list-item').forEach(function(item){
      var timer = null;
      item.addEventListener('touchstart', function(){
        timer = setTimeout(function(){
          item._longPressed = true;
          var idx   = parseInt(item.dataset.idx);
          var subid = item.dataset.subid;
          var subj  = subjects[idx];
          if(!subj) return;
          var ftTitle = document.getElementById('ftTitle');
          var ftMeta  = document.getElementById('ftMeta');
          var ftExtra = document.getElementById('ftExtra');
          var ftMask  = document.getElementById('ftMask');
          if(!ftTitle || !ftMask) return;
          ftTitle.textContent = subj.name || '';
          var p = subj.pctChg;
          ftMeta.textContent = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '';
          ftExtra.innerHTML  = '<div class="fulltext-loading">详情加载中…</div>';
          ftMask.classList.add('show');
          if(subid){
            fetch('/api/subject-query?id='+subid)
              .then(function(r){ return r.json(); })
              .then(function(d){
                var h = '';
                if(d.reason) h += '<div class="fulltext-reason">'+d.reason+'</div>';
                if(d.detail && d.detail.trim() && d.detail.trim()!=='<p><br></p>')
                  h += '<div class="fulltext-detail">'+d.detail+'</div>';
                ftExtra.innerHTML = h || '暂无详情';
              })
              .catch(function(){ ftExtra.innerHTML = ''; });
          } else { ftExtra.innerHTML = ''; }
        }, 500);
      });
      function cancelLong(){ clearTimeout(timer); setTimeout(function(){ item._longPressed = false; }, 100); }
      item.addEventListener('touchend',    cancelLong);
      item.addEventListener('touchmove',   cancelLong);
      item.addEventListener('touchcancel', cancelLong);
    });
  }

  function selectSubjectWith(item, subjects, chartEl, cache){
    var subid = item.dataset.subid;
    var idx   = parseInt(item.dataset.idx);
    var subj  = subjects[idx];
    if(!subj) return;

    // ── 宽屏：更新中间详情面板 ──
    var ndName = document.getElementById('ndName');
    var ndPct  = document.getElementById('ndPct');
    var ndDate = document.getElementById('ndDate');
    var ndBody = document.getElementById('ndBody');
    if(ndName && window.innerWidth >= 900){
      var p = subj.pctChg;
      var ps = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '';
      var pcls = p > 0 ? 'up' : p < 0 ? 'dn' : 'flat';
      ndName.textContent = subj.name || '';
      ndPct.className = 'tree-detail-pct ' + pcls;
      ndPct.textContent = ps;
      ndDate.textContent = subj.rankDate ? subj.rankDate.slice(0,10) : '';
      ndBody.innerHTML = '<div class="tree-detail-loading">详情加载中…</div>';
      if(subid){
        fetch('/api/subject-query?id='+subid)
          .then(function(r){ return r.json(); })
          .then(function(d){
            var h = '';
            if(d.reason){
              h += '<div class="tree-detail-section">';
              h += '<div class="tree-detail-label">驱动逻辑</div>';
              h += '<div class="tree-detail-text">'+d.reason+'</div>';
              h += '</div>';
            }
            if(d.detail && d.detail.trim() && d.detail.trim()!=='<p><br></p>'){
              h += '<div class="tree-detail-section">';
              h += '<div class="tree-detail-label">详细说明</div>';
              h += '<div class="tree-detail-text">'+d.detail+'</div>';
              h += '</div>';
            }
            ndBody.innerHTML = h || '<div class="tree-detail-loading">暂无详情</div>';
          })
          .catch(function(){ ndBody.innerHTML = ''; });
      } else { ndBody.innerHTML = ''; }
    }

    if(subid && !cache[subid]){
      chartEl.innerHTML = '<div class="tree-loading">加载中…</div>';
      fetch('/api/subject-child-tree?id='+subid)
        .then(function(r){ return r.json(); })
        .then(function(tree){
          cache[subid] = tree && tree.length ? tree : null;
          renderHTree(chartEl, subj, cache[subid]);
        })
        .catch(function(){ renderHTree(chartEl, subj, null); });
    } else {
      renderHTree(chartEl, subj, cache[subid] || null);
    }
  }

  function loadSubjectTree(){
    if(_treeListLoaded) return;
    _treeListLoaded = true;
    var listEl  = document.getElementById('treeList');
    var chartEl = document.getElementById('treeChart');
    if(!listEl || !chartEl) return;
    fetch('/api/subject-tree')
      .then(function(r){ return r.json(); })
      .then(function(days){
        if(!days || !days.length){ listEl.innerHTML='<div class="tree-loading">暂无数据</div>'; return; }
        var subjects = [];
        days.forEach(function(day){
          (day.children||[]).forEach(function(typeNode){
            (typeNode.children||[]).forEach(function(subj){ subjects.push(subj); });
          });
        });
        renderSubjectList(listEl, chartEl, subjects, _treeChartCache);
      })
      .catch(function(){ listEl.innerHTML='<div class="tree-loading">加载失败</div>'; });
  }

  function renderHTree(chartEl, subj, childTree){
    var rootPct = subj.pctChg;
    var rootPs  = rootPct != null ? ((rootPct>=0?'+':'')+rootPct.toFixed(2)+'%') : '';
    var rootCls = rootPct > 0 ? 'up' : rootPct < 0 ? 'dn' : 'flat';
    var _uid = 0;

    // 准备分组：支持任意深度
    var groups = childTree && childTree.length ? childTree : null;
    // 如果 childTree 根节点是题材本身（同名），就展开其 children
    if(groups && groups.length===1 && groups[0].name===subj.name){
      groups = groups[0].children && groups[0].children.length ? groups[0].children : groups;
    }
    if(!groups || !groups.length){
      if(subj.stocks && subj.stocks.length){
        groups = [{ name: '', pctChg: null, stocks: subj.stocks, children: [] }];
      } else {
        chartEl.innerHTML = '<div class="vtree-nodata">暂无子树数据</div>';
        return;
      }
    }

    // 递归渲染分组块（depth 控制缩进层级）
    function renderGroups(nodes, isTop, depth){
      if(depth === undefined) depth = 0;
      var html = '';
      var hasSingleAnon = nodes.length===1 && !nodes[0].name;
      nodes.forEach(function(g, gi){
        var gp   = g.pctChg;
        var gps  = gp != null ? ((gp>=0?'+':'')+gp.toFixed(2)+'%') : '';
        var gcls = gp > 0 ? 'up' : gp < 0 ? 'dn' : 'flat';
        var uid  = 'vg'+(++_uid);
        var children = g.children || [];
        var stocks   = g.stocks   || [];
        var isLast   = gi === nodes.length - 1;
        html += '<div class="vtree-group" data-depth="'+depth+'">';
        // 分组标题行
        if(!hasSingleAnon && g.name){
          var indent = depth * 20;
          html += '<div class="vtree-group-hd" data-uid="'+uid+'" data-depth="'+depth+'" style="padding-left:'+(14+indent)+'px">';
          html += '<span class="vtree-tree-line" style="left:'+(6+indent)+'px"></span>';
          html += '<span class="vtree-group-arrow open">►</span>';
          html += '<span class="vtree-group-name">'+g.name+'</span>';
          if(gps) html += '<span class="vtree-group-pct '+gcls+'">'+gps+'</span>';
          html += '</div>';
        }
        // 内容区（子分组或股票）
        html += '<div class="vtree-body" data-uid="'+uid+'">';
        if(children.length){
          html += renderGroups(children, false, depth + 1);
        } else {
          // 股票行
          var stockIndent = (hasSingleAnon ? 0 : (depth + 1)) * 20;
          stocks.forEach(function(s){
            var sid    = s.stockId || '';
            var sn     = s.stockName || s.name || '';
            var sp     = s.pctChg;
            var sps    = sp != null ? ((sp>=0?'+':'')+sp.toFixed(2)+'%') : '—';
            var scls   = sp > 0 ? 'up' : sp < 0 ? 'dn' : 'flat';
            // 屏蔽占位（夸克会员权限不足）：stockId=111 或 stockName=**** 视为未解析
            var isMasked = !sn || sn === '****' || sid === '111';
            var attr   = (sid && !isMasked) ? ' data-sid="'+sid+'"' : '';
            var reason = s.reason || '';
            html += '<div class="vtree-stock-row"'+attr+' style="padding-left:'+(14+stockIndent)+'px">';
            html += !isMasked
              ? '<div class="vtree-stock-name">'+sn+'</div>'
              : '<div class="vtree-stock-name is-masked" title="夸克会员权限不足，无法解析此股票（请检查夸克会员是否到期）">🔒 受限</div>';
            if(sid && !isMasked) html += '<div class="vtree-stock-code">'+sid+'</div>';
            if(reason) html += '<div class="vtree-stock-reason">'+reason+'</div>';
            html += '<div class="vtree-stock-pct '+scls+'"><span class="pct-val '+scls+'">'+sps+'</span></div>';
            html += '</div>';
          });
        }
        html += '</div>'; // vtree-body
        html += '</div>'; // vtree-group
      });
      return html;
    }

    // 根节点标题行
    var html = '<div class="vtree">';
    html += '<div class="vtree-root-hd">';
    html += '<span class="vtree-root-name">'+subj.name+'</span>';
    if(rootPs) html += '<span class="vtree-root-pct '+rootCls+'">'+rootPs+'</span>';
    html += '</div>';
    html += '<div class="vtree-col-hd">';
    html += '<div class="vtree-col-hd-name">股票名</div>';
    html += '<div class="vtree-col-hd-code">代码</div>';
    html += '<div class="vtree-col-hd-reason">驱动原因</div>';
    html += '<div class="vtree-col-hd-pct">涨跌幅</div>';
    html += '</div>';
    html += renderGroups(groups, true, 0);
    html += '</div>';
    chartEl.innerHTML = html;

    // ── 检测被屏蔽的股票名（夸克权限受限），显示「补全」按钮 ──
    var maskedCount = chartEl.querySelectorAll('.vtree-stock-name.is-masked').length;
    if(maskedCount > 0 && subj.subjectId){
      var _subid = subj.subjectId;
      var barEl = document.createElement('div');
      barEl.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 14px;background:#fff8ee;border-bottom:1px solid #ffe0b2';
      var tipSpan = document.createElement('span');
      tipSpan.style.cssText = 'font-size:12px;color:#e65100';
      tipSpan.textContent = '检测到 ' + maskedCount + ' 只屏蔽股票（夸克会员权限受限）';
      var btn = document.createElement('button');
      btn.textContent = '尝试调用夸克补全';
      btn.style.cssText = 'padding:4px 14px;font-size:12px;border:none;border-radius:4px;background:#ff9800;color:#fff;cursor:pointer;font-weight:600';
      btn.onmouseover = function(){ btn.style.background='#f57c00'; };
      btn.onmouseout  = function(){ btn.style.background='#ff9800'; };
      barEl.appendChild(tipSpan);
      barEl.appendChild(btn);
      chartEl.insertBefore(barEl, chartEl.firstChild);

      btn.addEventListener('click', function(){
        btn.disabled = true;
        btn.style.opacity = '0.6';
        btn.style.cursor = 'wait';
        tipSpan.textContent = '正在从夸克补全，请稍候…';
        fetch('/api/unmask-subject?id=' + _subid)
          .then(function(r){ return r.json(); })
          .then(function(res){
            if(res.ok){
              if(res.fixed === 0 && res.still_masked > 0){
                tipSpan.style.color = '#c62828';
                tipSpan.textContent = '补全失败：' + res.still_masked + ' 只仍被屏蔽（夸克会员可能已到期或权限不足）';
              } else {
                tipSpan.style.color = '#2e7d32';
                tipSpan.textContent = '补全完成！修复 ' + res.fixed + ' 只' +
                  (res.still_masked > 0 ? '，仍 ' + res.still_masked + ' 只无法解析（夸克会员权限不足）' : '');
              }
              btn.textContent = '刷新查看';
              btn.disabled = false;
              btn.style.opacity = '1';
              btn.style.cursor = 'pointer';
              btn.style.background = '#4caf50';
              btn.onclick = function(){
                if(_newChartCache) delete _newChartCache[_subid];
                if(_treeChartCache) delete _treeChartCache[_subid];
                fetch('/api/subject-child-tree?id='+_subid)
                  .then(function(r2){ return r2.json(); })
                  .then(function(tree){
                    if(_newChartCache) _newChartCache[_subid] = tree && tree.length ? tree : null;
                    if(_treeChartCache) _treeChartCache[_subid] = tree && tree.length ? tree : null;
                    renderHTree(chartEl, subj, tree && tree.length ? tree : null);
                  }).catch(function(){});
              };
            } else {
              tipSpan.style.color = '#c62828';
              tipSpan.textContent = '补全失败: ' + (res.error || '未知错误');
              btn.disabled = false;
              btn.style.opacity = '1';
              btn.style.cursor = 'pointer';
            }
          })
          .catch(function(err){
            tipSpan.style.color = '#c62828';
            tipSpan.textContent = '网络错误: ' + err.message;
            btn.disabled = false;
            btn.style.opacity = '1';
            btn.style.cursor = 'pointer';
          });
      });
    }

    // 分组标题行点击折叠
    chartEl.querySelectorAll('.vtree-group-hd').forEach(function(hd){
      hd.addEventListener('click', function(){
        var uid   = hd.dataset.uid;
        var arrow = hd.querySelector('.vtree-group-arrow');
        var body  = chartEl.querySelector('.vtree-body[data-uid="'+uid+'"]');
        if(!body) return;
        var collapsed = body.style.display === 'none';
        body.style.display = collapsed ? '' : 'none';
        if(arrow) arrow.classList.toggle('open', collapsed);
      });
    });

    // 异步刷新行情
    var ids = [];
    chartEl.querySelectorAll('[data-sid]').forEach(function(el){
      var sid = el.dataset.sid;
      if(sid && ids.indexOf(sid)===-1) ids.push(sid);
    });
    if(ids.length){
      for(var i=0;i<ids.length;i+=60){
        (function(chunk){
          fetch('/api/quotes?ids='+chunk.join(','))
            .then(function(r){ return r.json(); })
            .then(function(qt){ applyQuotesVtree(qt, chartEl); })
            .catch(function(){});
        })(ids.slice(i,i+60));
      }
    }
  }

  function applyQuotesVtree(qt, chartEl){
    Object.keys(qt).forEach(function(sid){
      var v = qt[sid][0];
      var pstr = (v>=0?'+':'')+v.toFixed(2)+'%';
      var cls  = v>0?'up': v<0?'dn':'flat';
      chartEl.querySelectorAll('[data-sid="'+sid+'"] .pct-val').forEach(function(el){
        el.textContent = pstr;
        el.className   = 'pct-val '+cls;
        var row = el.closest('.vtree-stock-row');
        if(row){
          var pctEl = row.querySelector('.vtree-stock-pct');
          if(pctEl) pctEl.className = 'vtree-stock-pct '+cls;
        }
      });
    });
  }

  // ── 题材领涨股异步加载 ─────────────────────────────
  function loadLeadStocks(){
    document.querySelectorAll('.card[data-subid]').forEach(function(card){
      var subid = card.dataset.subid;
      var date  = card.dataset.date;
      var bar   = document.getElementById('lead-' + subid);
      if(!subid || !bar) return;
      // 展开时才加载（防止首屏大量并发请求）
      card.addEventListener('toggle', function(){
        if(!card.open || bar.dataset.loaded) return;
        bar.dataset.loaded = '1';
        fetch('/api/subject-lead?id=' + subid + '&date=' + date)
          .then(function(r){ return r.json(); })
          .then(function(list){
            if(!list || !list.length){ bar.style.display='none'; return; }
            var html = '';
            list.slice(0, 6).forEach(function(s){
              var p = s.pctChg;
              var ps = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '';
              var dn = p != null && p < 0 ? ' dn' : '';
              html += '<span class="lead-tag'+dn+'">' + s.stockName + ' ' + ps + '</span>';
            });
            bar.innerHTML = html;
          })
          .catch(function(){ bar.style.display='none'; });
      }, {once: false});
    });
  }

  // ── 个股详情页关联题材列表 ───────────────────────
  function loadStockSubjects(){
    var wrap = document.getElementById('subjList');
    if(!wrap) return;
    var sid = document.body.dataset.sid;
    if(!sid){ wrap.innerHTML='<div class="kline-empty">无数据</div>'; return; }
    fetch('/api/stock-subjects?id=' + sid)
      .then(function(r){ return r.json(); })
      .then(function(list){
        if(!list || !list.length){ wrap.innerHTML='<div class="kline-empty">暂无关联题材</div>'; return; }
        var html = '';
        list.slice(0, 15).forEach(function(s){
          var p = s.pctChg;
          var ps = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '—';
          var cls = p > 0 ? 'up' : p < 0 ? 'dn' : 'flat';
          html += '<div class="subj-item">'
            + '<div class="subj-item-name">' + (s.name||s.subjectName||'') + '</div>'
            + '<div class="subj-item-pct ' + cls + '">' + ps + '</div>'
            + '</div>';
        });
        wrap.innerHTML = html;
      })
      .catch(function(){ wrap.innerHTML='<div class="kline-empty">加载失败</div>'; });
  }

  window.addEventListener('load', function(){
    initTabs();
    loadQuotes();
    initFullText();
    loadLeadStocks();
    initStockDetail();
    loadStockSubjects();
    // 初始 tab 触发加载（新题材默认直接加载，无需等待点击）
    var initTab = (location.search.match(/[?&]tab=([^&]+)/) || [])[1] || 'new';
    loadNewSubjects();
    if(initTab === 'cycle')   loadCycleTab();
    if(initTab === 'aigraph') loadAigraphTab();
  });
})();
