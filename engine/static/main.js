(function(){
// ── Tab 切换（纯前端，无刷新）──────────────────────────
  function initTabs(){
    var tabs  = document.querySelectorAll('.tab');
    var groups = document.querySelectorAll('.tab-pane');
    function activate(key){
      tabs.forEach(function(t){ t.classList.toggle('active', t.dataset.tab===key); });
      groups.forEach(function(g){ g.style.display = g.dataset.tab===key ? '' : 'none'; });
      history.replaceState(null,'','/?tab='+key);
      if(key === 'new')      loadNewSubjects();
      if(key === 'event')    loadEventTab();
      if(key === 'cycle')    loadCycleTab();
      if(key === 'aigraph')  loadAigraphTab();
      if(key === 'ime')      loadImeTab();
    }
    tabs.forEach(function(t){
      t.addEventListener('click', function(e){
        e.preventDefault();
        activate(t.dataset.tab);
      });
    });
  }

  // ── 驱动事件 Tab：懒加载（首次切换时才请求 HTML）────────
  var _eventLoaded = false;
  function loadEventTab(){
    if(_eventLoaded) return;
    _eventLoaded = true;
    var container = document.getElementById('eventBody');
    if(!container) return;
    fetch('/api/events')
      .then(function(r){ return r.text(); })
      .then(function(html){
        container.innerHTML = html;
        // 加载完卡片后立即拉行情
        loadQuotes();
        // 绑定全文弹窗
        initFullText();
      })
      .catch(function(){ container.innerHTML = '<div class="cards-loading">加载失败，请刷新重试</div>'; });
  }

  // ── 异步加载行情，填充涨跌幅 ──────────────────────────
  function loadQuotes(){
    // 收集所有 stock_id
    var ids = [];
    document.querySelectorAll('[data-sid]').forEach(function(el){
      var sid = el.dataset.sid;
      if(sid && ids.indexOf(sid)===-1) ids.push(sid);
    });
    if(!ids.length) return;
    // 分批，每批 60 个避免 URL 过长
    var batch = 60;
    for(var i=0;i<ids.length;i+=batch){
      (function(chunk){
        fetch('/api/quotes?ids='+chunk.join(','))
          .then(function(r){ return r.json(); })
          .then(function(qt){ applyQuotes(qt); })
          .catch(function(){});
      })(ids.slice(i,i+batch));
    }
  }

  function applyQuotes(qt){
    // qt: {"600519": [pct, price], ...}
    Object.keys(qt).forEach(function(sid){
      var v   = qt[sid][0];
      var price = qt[sid][1];
      var pstr = (v>=0?'+':'')+v.toFixed(2)+'%';
      var cls  = v>0?'up': v<0?'dn':'flat';
      document.querySelectorAll('[data-sid="'+sid+'"]').forEach(function(el){
        el.querySelector('.pct-val').textContent  = pstr;
        el.querySelector('.pct-val').className    = 'pct-val '+cls;
        var priceEl = el.querySelector('.price-val');
        if(priceEl){ priceEl.textContent = price.toFixed(2); }
      });
    });
  }

  // ── 点击题材名显示全文弹窗 ─────────────────────────────
  function initFullText(){
    var mask  = document.getElementById('ftMask');
    var title = document.getElementById('ftTitle');
    var meta  = document.getElementById('ftMeta');
    var extra = document.getElementById('ftExtra');
    var close = document.getElementById('ftClose');
    function show(text, sub, subid){
      title.textContent = text;
      meta.textContent  = sub || '';
      extra.innerHTML   = '';
      mask.classList.add('show');
      // 如果有 subjectId，异步拉取题材详情
      if(subid){
        extra.innerHTML = '<div class="fulltext-loading">详情加载中…</div>';
        fetch('/api/subject-query?id=' + subid)
          .then(function(r){ return r.json(); })
          .then(function(d){
            var html = '';
            if(d.reason){
              html += '<div class="fulltext-reason">' + d.reason + '</div>';
            }
            if(d.detail && d.detail.trim() && d.detail.trim() !== '<p><br></p>' && d.detail.trim() !== '<p></p>'){
              html += '<div class="fulltext-detail">' + d.detail + '</div>';
            }
            extra.innerHTML = html || '';
          })
          .catch(function(){ extra.innerHTML = ''; });
      }
    }
    function hide(){ mask.classList.remove('show'); }
    close.addEventListener('click', hide);
    mask.addEventListener('click', function(e){ if(e.target===mask) hide(); });
    document.addEventListener('keydown', function(e){ if(e.key==='Escape') hide(); });
    document.addEventListener('click', function(e){
      var el = e.target.closest('.card-name');
      if(!el) return;
      e.stopPropagation();
      e.preventDefault();
      var full  = el.dataset.full || el.textContent;
      var sub   = el.closest('.card-info') &&
                  el.closest('.card-info').querySelector('.card-sub');
      var card  = el.closest('.card[data-subid]');
      var subid = card ? card.dataset.subid : null;
      show(full, sub ? sub.textContent : '', subid);
    }, true);
  }

  // ── 详情页：日K图 + 主营业务 ────────────────────────────
  function initStockDetail(){
    var canvas = document.getElementById('klineCanvas');
    var bizWrap = document.getElementById('bizBars');
    if(!canvas && !bizWrap) return;  // 不在详情页则跳过

    var sid = document.body.dataset.sid;
    if(!sid) return;

    fetch('/api/stock-detail?id=' + sid)
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(d.kline) drawKline(canvas, d.kline);
        if(d.biz && bizWrap) drawBiz(bizWrap, d.biz);
      })
      .catch(function(){});
  }

  function drawKline(canvas, data){
    if(!canvas) return;
    var fields = data.fields || [];
    var items  = data.items  || [];
    if(!items.length){ canvas.parentNode.innerHTML='<div class="kline-empty">暂无日K数据</div>'; return; }
    var fi = {};
    fields.forEach(function(f,i){ fi[f]=i; });
    // 取最近 60 条
    var rows = items.slice(-60);
    var closes = rows.map(function(r){ return r[fi['close']]; });
    var pcts   = rows.map(function(r){ return r[fi['pct_chg']]; });
    var dates  = rows.map(function(r){ return String(r[fi['trade_date']] || '').slice(4); });
    var W = canvas.offsetWidth || 320;
    var H = 160;
    canvas.width  = W;
    canvas.height = H;
    var ctx = canvas.getContext('2d');
    var minC = Math.min.apply(null, closes);
    var maxC = Math.max.apply(null, closes);
    var range = maxC - minC || 1;
    var pad = {top:10, bottom:22, left:4, right:4};
    var iW = (W - pad.left - pad.right) / rows.length;
    ctx.clearRect(0, 0, W, H);
    // 背景网格
    ctx.strokeStyle = '#f0f2f5'; ctx.lineWidth = 1;
    [0.25, 0.5, 0.75].forEach(function(r){
      var y = pad.top + (1-r)*(H - pad.top - pad.bottom);
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W-pad.right, y); ctx.stroke();
    });
    // 折线
    ctx.beginPath(); ctx.lineWidth = 1.5;
    rows.forEach(function(row, i){
      var x = pad.left + i * iW + iW/2;
      var y = pad.top + (1 - (closes[i]-minC)/range) * (H - pad.top - pad.bottom);
      var p = pcts[i];
      ctx.strokeStyle = p >= 0 ? '#e53935' : '#26a69a';
      if(i === 0){ ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
    // 日期标签（首尾+中间）
    ctx.fillStyle='#bbb'; ctx.font='10px sans-serif'; ctx.textAlign='center';
    [0, Math.floor(rows.length/2), rows.length-1].forEach(function(i){
      if(i < dates.length){
        var x = pad.left + i*iW + iW/2;
        ctx.fillText(dates[i], x, H-4);
      }
    });
  }

  function drawBiz(wrap, biz){
    // biz: [{label, amount}] sorted desc
    if(!biz || !biz.length){ wrap.innerHTML='<div style="color:#ccc;font-size:13px;padding:8px">暂无主营数据</div>'; return; }
    var max = biz[0].amount || 1;
    var html = '';
    biz.forEach(function(b){
      var pct = Math.round(b.amount / max * 100);
      var val = b.amount >= 1e8 ? (b.amount/1e8).toFixed(1)+'亿' : (b.amount/1e4).toFixed(0)+'万';
      html += '<div class="biz-row">'
        +'<div class="biz-label" title="'+b.label+'">'+b.label+'</div>'
        +'<div class="biz-bar-bg"><div class="biz-bar-fill" style="width:'+pct+'%"></div></div>'
        +'<div class="biz-val">'+val+'</div>'
        +'</div>';
    });
    wrap.innerHTML = html;
  }

  // ── 大盘指数 sparkline ───────────────────────────────
  function drawSparks(){
    document.querySelectorAll('canvas.mkt-spark').forEach(function(c){
      var raw = c.dataset.spark;
      var dire = c.dataset.cls || '';
      if(!raw) return;
      var vals;
      try{ vals = JSON.parse(raw); } catch(e){ return; }
      if(!vals || vals.length < 2) return;
      // 利用 CSS 实际宽度
      var W = c.parentNode.clientWidth || 200;
      var H = 28;
      c.width  = W;
      c.height = H;
      var ctx = c.getContext('2d');
      var min = Math.min.apply(null, vals);
      var max = Math.max.apply(null, vals);
      var rng = max - min || 1;
      var color = dire === 'up' ? '#e53935' : dire === 'dn' ? '#26a69a' : '#aaa';
      var fillColor = dire === 'up' ? 'rgba(229,57,53,.08)' : dire === 'dn' ? 'rgba(38,166,154,.08)' : 'rgba(0,0,0,.04)';
      ctx.clearRect(0, 0, W, H);
      // 填充区域
      ctx.beginPath();
      vals.forEach(function(v, i){
        var x = i / (vals.length - 1) * W;
        var y = H - 2 - (v - min) / rng * (H - 4);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
      ctx.fillStyle = fillColor;
      ctx.fill();
      // 折线
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      vals.forEach(function(v, i){
        var x = i / (vals.length - 1) * W;
        var y = H - 2 - (v - min) / rng * (H - 4);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  // ── 题材轮动 Tab ────────────────────────────────────────
  var _cycleLoaded = false;
  function loadCycleTab(){
    if(_cycleLoaded) return;
    _cycleLoaded = true;
    var wrap = document.getElementById('cycleBody');
    if(!wrap) return;
    fetch('/api/subject-cycle')
      .then(function(r){ return r.json(); })
      .then(function(days){
        if(!days || !days.length){
          wrap.innerHTML = '<div class="cycle-loading">暂无轮动数据</div>';
          return;
        }
        var cols = days.slice(0, 10);
        var maxRows = 0;
        cols.forEach(function(c){ if((c.rows||[]).length > maxRows) maxRows = c.rows.length; });
        maxRows = Math.min(maxRows, 25);

        var html = '<table class="cycle-table"><thead><tr><th>排名</th>';
        cols.forEach(function(c){ html += '<th>'+c.date.slice(5)+'</th>'; });
        html += '</tr></thead><tbody>';

        for(var i = 0; i < maxRows; i++){
          html += '<tr><td>'+(i+1)+'</td>';
          cols.forEach(function(c){
            var item = (c.rows||[])[i];
            if(!item){ html += '<td></td>'; return; }
            var name = item.name || item.subjectName || '';
            var p = (item.pctChg === '' || item.pctChg == null) ? null : Number(item.pctChg);
            if(p != null && !isFinite(p)) p = null;
            var ps  = p != null ? ((p>=0?'+':'')+p.toFixed(2)+'%') : '';
            var pcls = p > 0 ? 'up' : p < 0 ? 'dn' : 'flat';
            var cntRaw = item.limitUpTimes;
            var cnt = (cntRaw === '' || cntRaw == null) ? null : Number(cntRaw);
            html += '<td><div class="cycle-cell">';
            html += '<div class="cycle-cell-name" title="'+_esc(name)+'">'+_esc(name)+'</div>';
            if(cnt) html += '<div class="cycle-cell-cnt">'+cnt+'</div>';
            if(ps)  html += '<div class="cycle-cell-pct '+pcls+'">'+ps+'</div>';
            html += '</div></td>';
          });
          html += '</tr>';
        }
        html += '</tbody></table>';
        wrap.outerHTML = html;
      })
      .catch(function(){ wrap.innerHTML = '<div class="cycle-loading">加载失败</div>'; });
  }

  // ── 研报分析 Tab（ZSXQ 风格：分类Tabs + 信息流 + 解读面板）──────
  var _imeLoaded  = false;
  var _imeCat     = '';
  var _imeQ       = '';
  var _imeReports = [];
  var _imePosts   = [];
  var _imeStats   = {};
  var _imeFeedPg  = 1;
  var _imePerPage = 30;

  function loadImeTab(){
    if(_imeLoaded) return;
    _imeLoaded = true;
    var wrap = document.getElementById('imeBody');
    if(!wrap) return;

    wrap.innerHTML =
      '<div class="ime2-root">'
        +'<div class="ime2-overview" id="ime2Overview"><span>加载数据库视图...</span></div>'
        +'<div class="ime2-tabs-bar">'
          +'<div class="ime2-tabs" id="ime2Tabs"><span class="ime2-tab active" data-cat="">最新</span></div>'
          +'<div class="ime2-search-wrap"><input id="ime2Search" class="ime2-search" placeholder="🔍 搜索研报/帖子"></div>'
        +'</div>'
        +'<div class="ime2-body">'
          +'<div class="ime2-feed" id="ime2Feed"><div class="ime2-hint">加载中…</div></div>'
          +'<div class="ime2-reader" id="ime2Reader"><div class="ime2-reader-hint">← 点击左侧研报<br>查看 AI 解读</div></div>'
        +'</div>'
      +'</div>';

    // 搜索防抖
    var inp = document.getElementById('ime2Search');
    if(inp){
      var _st;
      inp.addEventListener('input', function(){
        clearTimeout(_st);
        _st = setTimeout(function(){ _imeQ = inp.value.trim().toLowerCase(); _imeFeedPg = 1; _imeRenderFeed(); }, 300);
      });
    }

    // 分类 Tab 点击（事件委托）
    document.getElementById('ime2Tabs').addEventListener('click', function(e){
      var t = e.target.closest('.ime2-tab');
      if(!t) return;
      document.querySelectorAll('.ime2-tab').forEach(function(x){ x.classList.remove('active'); });
      t.classList.add('active');
      _imeCat = t.dataset.cat;
      _imeFeedPg = 1;
      _imeRenderFeed();
    });

    // 并发加载全量数据
    Promise.all([
      fetch('/api/ime-reports?limit=12000').then(function(r){ return r.json(); }),
      fetch('/api/ime-posts?limit=20000').then(function(r){ return r.json(); })
    ]).then(function(res){
      _imeReports = (res[0].items || []).map(function(r){ r._type = 'report'; return r; });
      _imePosts   = (res[1].items || []).map(function(p){ p._type = 'post';   return p; });
      _imeStats   = {
        reportsShown: _imeReports.length,
        reportsTotal: res[0].total || _imeReports.length,
        postsShown: _imePosts.length,
        postsTotal: res[1].total || _imePosts.length,
        updatedAt: res[0].updated_at || res[1].updated_at || '',
        meta: res[0].meta || res[1].meta || {}
      };
      _imeRenderOverview();
      _imeBuildCatTabs();
      _imeRenderFeed();
    }).catch(function(){
      var f = document.getElementById('ime2Feed');
      if(f) f.innerHTML = '<div class="ime2-hint">加载失败，请刷新页面</div>';
    });
  }

  function _imeBuildCatTabs(){
    var tabs = document.getElementById('ime2Tabs');
    if(!tabs) return;
    var seen = {}, cats = [];
    _imeReports.forEach(function(r){ var c = r.category||''; if(c && !seen[c]){ seen[c]=1; cats.push(c); } });
    _imePosts.forEach(function(p){ var h = p.hashtag||''; if(h && !seen[h]){ seen[h]=1; cats.push(h); } });
    var html = '<span class="ime2-tab active" data-cat="">最新</span>';
    cats.forEach(function(c){ html += '<span class="ime2-tab" data-cat="'+_esc(c)+'">'+_esc(c)+'</span>'; });
    tabs.innerHTML = html;
  }

  function _imeRenderOverview(){
    var el = document.getElementById('ime2Overview');
    if(!el) return;
    var meta = _imeStats.meta || {};
    var latestPost = meta.latest_post_time || '';
    var latestReport = meta.latest_report_time || '';
    el.innerHTML =
      '<span class="ime2-ov-main">数据库视图</span>'
      +'<span>研报 '+_fmtNum(_imeStats.reportsShown||0)+' / '+_fmtNum(_imeStats.reportsTotal||0)+'</span>'
      +'<span>星球帖 '+_fmtNum(_imeStats.postsShown||0)+' / '+_fmtNum(_imeStats.postsTotal||0)+'</span>'
      +'<span>附件 '+_fmtNum(meta.post_files_total||0)+'</span>'
      +'<span>图片 '+_fmtNum(meta.post_images_total||0)+'</span>'
      +'<span>链接 '+_fmtNum(meta.post_links_total||0)+'</span>'
      +(latestReport ? '<span>最新分析 '+_fmtTime(latestReport)+'</span>' : '')
      +(latestPost ? '<span>最新帖子 '+_fmtTime(latestPost)+'</span>' : '');
  }

  function _imeGetFeed(){
    var all = [];
    _imeReports.forEach(function(r){
      if(_imeCat && r.category !== _imeCat) return;
      if(_imeQ){
        var tx = ((r.file_name||'')+(r.title_cn||'')+(r.s_one_liner||'')+(r.s_catalyst||'')+(r.category||'')+(r.source_hashtag||'')+(r.source_topic_id||'')).toLowerCase();
        if(tx.indexOf(_imeQ) === -1) return;
      }
      all.push(r);
    });
    _imePosts.forEach(function(p){
      if(_imeCat && p.hashtag !== _imeCat) return;
      if(_imeQ){
        var tx = ((p.title||'')+(p.text_preview||'')+(p.hashtag||'')+(p.author_name||'')).toLowerCase();
        if(tx.indexOf(_imeQ) === -1) return;
      }
      all.push(p);
    });
    all.sort(function(a, b){
      var ta = a.source_time || a.analyzed_at || a.create_time || '';
      var tb = b.source_time || b.analyzed_at || b.create_time || '';
      return tb < ta ? -1 : tb > ta ? 1 : 0;
    });
    return all;
  }

  function _imeRenderFeed(){
    var feed = document.getElementById('ime2Feed');
    if(!feed) return;
    var all   = _imeGetFeed();
    var total = all.length;
    var start = (_imeFeedPg - 1) * _imePerPage;
    var page  = all.slice(start, start + _imePerPage);
    var pages = Math.ceil(total / _imePerPage) || 1;

    if(!total){ feed.innerHTML = '<div class="ime2-hint">暂无数据</div>'; return; }

    var html = '<div class="ime2-count">共 '+total+' 条</div>';
    page.forEach(function(item, i){
      var gi = start + i;
      if(item._type === 'report'){
        var cat = item.category ? '<span class="ime2-cat-tag">'+_esc(item.category)+'</span>' : '';
        var dt  = _fmtTime(item.source_time || item.analyzed_at || '');
        // 中文标题优先：title_cn → s_one_liner → file_name
        var cnTitle = (item.title_cn || '').replace(/\*\*/g,'').trim()
                   || _cleanMd(item.s_one_liner||'').slice(0,80)
                   || '';
        var titleHtml = '';
        if(cnTitle){
          titleHtml = '<div class="ime2-card-title" title="'+_esc(item.file_name||'')+'">'+_esc(_trunc(cnTitle, 55))+'</div>'
            +'<div class="ime2-card-sub">'+_esc(_trunc(item.file_name||'', 55))+'</div>';
        } else {
          titleHtml = '<div class="ime2-card-title" title="'+_esc(item.file_name||'')+'">'+_trunc(item.file_name||'', 46)+'</div>';
        }
        var srcBadge = _imeSrcBadge(item);
        var reportBadges = '<div class="ime2-minirow">'
          +(item.source_hashtag ? '<span>'+_esc(item.source_hashtag)+'</span>' : '')
          +(item.text_length ? '<span>'+_fmtNum(item.text_length)+'字</span>' : '')
          +(item.prompt_mode ? '<span>'+_esc(item.prompt_mode)+'</span>' : '')
          +(item.source_topic_id ? '<span>星球源</span>' : '')
          +'</div>';
        html += '<div class="ime2-card ime2-rc" data-gi="'+gi+'">'
          +'<div class="ime2-card-meta">'+cat+srcBadge+'<span class="ime2-ctype">研报</span>'
          +(item.download_url ? '<span class="ime2-card-dl-icon" title="可下载">📄</span>' : '')
          +'<span class="ime2-card-dt">'+dt+'</span></div>'
          +titleHtml
          +reportBadges
          +(item.s_one_liner ? '<div class="ime2-card-summ">'+_esc(_cleanMd(item.s_one_liner))+'</div>' : '')
          +(item.s_action    ? '<div class="ime2-card-act">'+_esc(_cleanMd(item.s_action))+'</div>' : '')
          +'</div>';
      } else {
        var ht = item.hashtag ? '<span class="ime2-ht-tag">'+_esc(item.hashtag)+'</span>' : '';
        var dt = _fmtTime(item.create_time||'');
        // 正文：去掉 ##hashtag## 前缀
        var bodyText = (item.text_preview||'').replace(/^\s*#{1,2}[^#]+#{1,2}\s*/,'').trim();
        // 文件附件列表
        var filesHtml = '';
        if(item.files && item.files.length){
          filesHtml = '<div class="ime2-card-files">';
          item.files.forEach(function(f){
            var kb = f.size ? Math.round(f.size/1024)+'KB' : '';
            filesHtml += '<div class="ime2-card-file">📄 <span class="ime2-cf-name">'+_esc(_trunc(f.name,60))+'</span>'
              +(kb ? ' <span class="ime2-cf-size">'+kb+'</span>' : '')+'</div>';
          });
          filesHtml += '</div>';
        }
        // 如果正文空但有文件，用文件名做标题
        var postTitle = item.title || '';
        if(!postTitle && !bodyText && item.files && item.files.length){
          postTitle = item.files[0].name;
        }
        var postBadges = '<div class="ime2-minirow">'
          +'<span>附件 '+(item.file_count || (item.files||[]).length || 0)+'</span>'
          +'<span>图片 '+(item.image_count || (item.images||[]).length || 0)+'</span>'
          +'<span>链接 '+(item.link_count || (item.links||[]).length || 0)+'</span>'
          +(item.likes_count ? '<span>赞 '+_fmtNum(item.likes_count)+'</span>' : '')
          +(item.comments_count ? '<span>评 '+_fmtNum(item.comments_count)+'</span>' : '')
          +(item.readers_count ? '<span>读 '+_fmtNum(item.readers_count)+'</span>' : '')
          +(item.fetched_at ? '<span>抓取 '+_fmtTime(item.fetched_at)+'</span>' : '')
          +'</div>';
        html += '<div class="ime2-card ime2-pc" data-gi="'+gi+'">'
          +'<div class="ime2-card-meta">'+ht+'<span class="ime2-ctype ime2-ctype-post">帖子</span>'
          +'<span class="ime2-card-author">'+_esc(item.author_name||'')+'</span>'
          +'<span class="ime2-card-dt">'+dt+'</span></div>'
          +(postTitle ? '<div class="ime2-card-title">'+_esc(_trunc(postTitle,70))+'</div>' : '')
          +postBadges
          +(bodyText ? '<div class="ime2-card-body">'+_esc(_trunc(bodyText, 200))+'</div>' : '')
          +filesHtml
          +(item.images && item.images.length ? '<div class="ime2-card-imgs">'
            +item.images.slice(0,3).map(function(img){return '<img class="ime2-thumb" src="'+_esc(img.url)+'" loading="lazy" onerror="this.style.display=\'none\'">';}).join('')
            +(item.images.length>3 ? '<span class="ime2-thumb-more">+'+(item.images.length-3)+'</span>' : '')
            +'</div>' : '')
          +'</div>';
      }
    });

    if(pages > 1){
      html += '<div class="ime2-pager">';
      if(_imeFeedPg > 1) html += '<span class="ime2-pg-btn" data-pg="'+(_imeFeedPg-1)+'">‹ 上一页</span>';
      html += '<span class="ime2-pg-cur">'+_imeFeedPg+' / '+pages+'</span>';
      if(_imeFeedPg < pages) html += '<span class="ime2-pg-btn" data-pg="'+(_imeFeedPg+1)+'">下一页 ›</span>';
      html += '</div>';
    }

    feed.innerHTML = html;
    feed.scrollTop = 0;

    feed.onclick = function(e){
      var pb = e.target.closest('.ime2-pg-btn');
      if(pb){ _imeFeedPg = parseInt(pb.dataset.pg, 10); _imeRenderFeed(); return; }
      var rc = e.target.closest('.ime2-rc');
      if(rc){ _imeOpenReader(parseInt(rc.dataset.gi, 10)); return; }
      var pc = e.target.closest('.ime2-pc');
      if(pc){ _imeOpenPost(parseInt(pc.dataset.gi, 10)); }
    };
  }

  // 渲染一个 key-value 对象为子段 HTML
  function _imeKvSection(obj, labelMap){
    if(!obj || typeof obj !== 'object') return '';
    var html = '';
    for(var k in labelMap){
      var v = obj[k];
      if(v && typeof v === 'string' && v.replace(/\*+/g,'').trim()){
        html += '<div class="ime2-kv"><span class="ime2-kv-k">'+labelMap[k]+'</span><span class="ime2-kv-v">'+_esc(_cleanMd(v))+'</span></div>';
      }
    }
    return html;
  }

  function _imeOpenPost(gi){
    var all  = _imeGetFeed();
    var item = all[gi];
    if(!item || item._type === 'report') return;

    document.querySelectorAll('.ime2-rc,.ime2-pc').forEach(function(c){ c.classList.remove('ime2-rc-sel'); });
    var el = document.querySelector('.ime2-pc[data-gi="'+gi+'"]');
    if(el) el.classList.add('ime2-rc-sel');

    var reader = document.getElementById('ime2Reader');
    if(!reader) return;

    var ht = item.hashtag ? '<span class="ime2-ht-tag">'+_esc(item.hashtag)+'</span>' : '';
    var dt = _fmtTime(item.create_time||'');
    var hdHtml = '<div class="ime2-ri-hd">'+ht
      +'<span class="ime2-ctype ime2-ctype-post">帖子</span>'
      +'<span class="ime2-card-author">'+_esc(item.author_name||'')+'</span>'
      +'<span class="ime2-ri-dt">'+dt+'</span></div>';

    // 正文
    var bodyText = (item.text_preview||'').replace(/^\s*#{1,2}[^#]+#{1,2}\s*/,'').trim();
    var bodyHtml = bodyText ? '<div class="ime2-post-body">'+_esc(bodyText).replace(/\n/g,'<br>')+'</div>' : '';

    // 文件列表
    var filesHtml = '';
    if(item.files && item.files.length){
      filesHtml = '<div class="ime2-post-sec"><div class="ime2-rl">📎 附件 ('+item.files.length+')</div>';
      item.files.forEach(function(f){
        var kb = f.size ? (f.size >= 1048576 ? (f.size/1048576).toFixed(1)+'MB' : Math.round(f.size/1024)+'KB') : '';
        filesHtml += '<div class="ime2-post-file">'
          +'<span class="ime2-pf-icon">📄</span>'
          +'<span class="ime2-pf-name">'+_esc(f.name)+'</span>'
          +(kb ? '<span class="ime2-pf-size">'+kb+'</span>' : '')
          +'</div>';
      });
      filesHtml += '</div>';
    }

    // 链接列表
    var linksHtml = '';
    if(item.links && item.links.length){
      linksHtml = '<div class="ime2-post-sec"><div class="ime2-rl">🔗 链接</div>';
      item.links.forEach(function(url){
        var short = url.length > 60 ? url.slice(0,60)+'...' : url;
        linksHtml += '<div class="ime2-post-link"><a href="'+_esc(url)+'" target="_blank">'+_esc(short)+'</a></div>';
      });
      linksHtml += '</div>';
    }

    // 图片
    var imgsHtml = '';
    if(item.images && item.images.length){
      imgsHtml = '<div class="ime2-post-sec"><div class="ime2-rl">🖼 图片 ('+item.images.length+')</div><div class="ime2-post-imgs">';
      item.images.forEach(function(img){
        imgsHtml += '<img class="ime2-post-img" src="'+_esc(img.url)+'" loading="lazy" onerror="this.style.display=\'none\'">';
      });
      imgsHtml += '</div></div>';
    }

    // OCR/图片分析
    var ocrHtml = '';
    if(item.image_analysis){
      ocrHtml = '<div class="ime2-post-sec"><div class="ime2-rl">📝 图片文字识别</div>'
        +'<div class="ime2-post-ocr">'+_esc(item.image_analysis).replace(/\n/g,'<br>')+'</div></div>';
    }

    // 互动数据
    var statHtml = '<div class="ime2-post-stats">'
      +(item.likes_count ? '❤ '+item.likes_count+' ' : '')
      +(item.readers_count ? '👀 '+item.readers_count : '')
      +'</div>';

    reader.innerHTML = '<div class="ime2-ri">'
      +hdHtml+bodyHtml+filesHtml+linksHtml+imgsHtml+ocrHtml+statHtml
      +'</div>';
  }

  function _imeOpenReader(gi){
    var all  = _imeGetFeed();
    var item = all[gi];
    if(!item || item._type !== 'report') return;

    document.querySelectorAll('.ime2-rc').forEach(function(c){ c.classList.remove('ime2-rc-sel'); });
    var el = document.querySelector('.ime2-rc[data-gi="'+gi+'"]');
    if(el) el.classList.add('ime2-rc-sel');

    var reader = document.getElementById('ime2Reader');
    if(!reader) return;

    // ── 标题区 ──
    var hdHtml = '<div class="ime2-ri-hd">'
      +(item.category ? '<span class="ime2-cat-tag">'+_esc(item.category)+'</span>' : '')
      +_imeSrcBadge(item)
      +'<span class="ime2-ri-dt">'+_fmtTime(item.analyzed_at||'')+'</span>'
      +'</div>';

    // 中文标题优先，英文文件名做副标题
    var cnT = (item.title_cn||'').replace(/\*\*/g,'').trim() || _cleanMd(item.s_one_liner||'').slice(0,80) || '';
    var fnHtml = '';
    if(cnT){
      fnHtml = '<div class="ime2-ri-fn">'+_esc(cnT)+'</div>'
        +'<div class="ime2-ri-fn-sub">'+_esc(item.file_name||'')+'</div>';
    } else {
      fnHtml = '<div class="ime2-ri-fn">'+_esc(item.file_name||'')+'</div>';
    }

    // ── 第一部分：一句话逻辑 ──
    var s1Html = '';
    if(item.s_one_liner) s1Html += '<div class="ime2-rs"><div class="ime2-rl">核心逻辑</div><div class="ime2-rt">'+_esc(_cleanMd(item.s_one_liner))+'</div></div>';
    if(item.s_catalyst)  s1Html += '<div class="ime2-rs"><div class="ime2-rl ime2-rl-c">催化剂</div><div class="ime2-rt">'+_esc(_cleanMd(item.s_catalyst))+'</div></div>';

    // ── 第二部分：利益测算 ──
    var bc = item.s_benefit_calc || {};
    var bcHtml = _imeKvSection(bc, {formula:'增量公式', rating:'重要程度', window:'时间窗口'});
    if(bcHtml) bcHtml = '<div class="ime2-rs"><div class="ime2-rl ime2-rl-b">利益测算</div>'+bcHtml+'</div>';

    // ── 第三部分：风险清单 ──
    var rl = item.s_risk_list || {};
    var rlHtml = _imeKvSection(rl, {main_risk:'最大风险', valuation:'估值水位', us_barrier:'美国阻力', cn_barrier:'中国阻力'});
    if(rlHtml) rlHtml = '<div class="ime2-rs"><div class="ime2-rl ime2-rl-r">风险清单</div>'+rlHtml+'</div>';

    // ── 第四部分：行动建议 ──
    var actHtml = '';
    if(item.s_action) actHtml += '<div class="ime2-rs"><div class="ime2-rl ime2-rl-a">行动建议</div><div class="ime2-rt ime2-rt-a">'+_esc(_cleanMd(item.s_action))+'</div></div>';
    if(item.s_key_metric) actHtml += '<div class="ime2-rs"><div class="ime2-rl">跟踪指标</div><div class="ime2-rt">'+_esc(_cleanMd(item.s_key_metric))+'</div></div>';

    // ── 产业链推演：逻辑链 ──
    var chains = item.i_logic_chains || [];
    var chainHtml = '';
    if(chains.length){
      chainHtml = '<div class="ime2-rs"><div class="ime2-rl ime2-rl-i">逻辑链推演</div>';
      chains.forEach(function(c, idx){
        chainHtml += '<div class="ime2-ci">'
          +'<div class="ime2-ci-no">'+(idx+1)+'</div>'
          +'<div class="ime2-ci-body">'
            +(c.detail ? '<div class="ime2-ci-d">'+_esc(_cleanMd(c.detail))+'</div>' : '')
            +(c.chain  ? '<div class="ime2-cm">'+_esc(_cleanMd(c.chain))+'</div>' : '')
            +(c.conclusion ? '<div class="ime2-cc">→ '+_esc(_cleanMd(c.conclusion))+'</div>' : '')
          +'</div></div>';
      });
      chainHtml += '</div>';
    }

    // ── 个股量化测算 ──
    var sc = item.i_stock_calc || {};
    var scHtml = _imeKvSection(sc, {anchor:'事件锚点', upstream:'上游驱动力', mid:'中间变量', downstream:'下游弹性', calc:'营收增量', profit:'利润弹性', outlook:'后市评价'});
    if(scHtml) scHtml = '<div class="ime2-rs"><div class="ime2-rl ime2-rl-i">个股量化测算</div>'+scHtml+'</div>';

    // ── 中美市场对比 ──
    var cu = item.i_china_us || {};
    var cuHtml = _imeKvSection(cu, {us_impact:'美国市场', cn_impact:'中国市场', cn_replace:'国产替代'});
    if(cuHtml) cuHtml = '<div class="ime2-rs"><div class="ime2-rl">中美市场对比</div>'+cuHtml+'</div>';

    // ── 下载链接 ──
    var dlHtml = item.download_url
      ? '<div class="ime2-rs ime2-dl-row"><a class="ime2-dl" href="'+item.download_url+'" target="_blank">⬇ 下载原始研报</a>'
        +'<a class="ime2-dl-open" href="'+item.download_url+'" target="_blank">在浏览器中打开</a></div>'
      : '';

    reader.innerHTML =
      '<div class="ime2-ri">'
        +hdHtml + fnHtml + dlHtml
        +s1Html + bcHtml + rlHtml + actHtml
        +chainHtml + scHtml + cuHtml
      +'</div>';
  }

  // ── 研报来源机构识别 ──
  var _imeSrcRules = [
    {m:'Barclays',        n:'巴克莱',   c:'#00aeef'},
    {m:'Goldman',         n:'高盛',     c:'#6b8cce'},
    {m:'Morgan Stanley',  n:'大摩',     c:'#002d62'},
    {m:'JPMorgan',        n:'小摩',     c:'#003087'},
    {m:'JP Morgan',       n:'小摩',     c:'#003087'},
    {m:'UBS',             n:'瑞银',     c:'#e60000'},
    {m:'HSBC',            n:'汇丰',     c:'#db0011'},
    {m:'Citi',            n:'花旗',     c:'#003b70'},
    {m:'Bloomberg',       n:'彭博',     c:'#f58220'},
    {m:'Needham',         n:'尼德汉',   c:'#2e7d32'},
    {m:'Deutsche',        n:'德银',     c:'#0018a8'},
    {m:'BofA',            n:'美银',     c:'#c41230'},
    {m:'Bank of America', n:'美银',     c:'#c41230'},
    {m:'Credit Suisse',   n:'瑞信',     c:'#003399'},
    {m:'Bernstein',       n:'伯恩斯坦', c:'#7b1fa2'},
    {m:'Jefferies',       n:'杰富瑞',   c:'#00695c'},
    {m:'Wells Fargo',     n:'富国',     c:'#bb0000'},
    {m:'Wells',           n:'富国',     c:'#bb0000'},
    {m:'RBC',             n:'加皇资本', c:'#0051a5'},
    {m:'Mizuho',          n:'瑞穗',     c:'#003d6b'},
    {m:'Piper',           n:'派杰',     c:'#4a7c59'},
    {m:'Wolfe',           n:'沃尔夫',   c:'#616161'},
    {m:'KeyBanc',         n:'凯银资本', c:'#0f5132'},
    {m:'Wedbush',         n:'韦德布什', c:'#1565c0'},
    {m:'Stifel',          n:'斯蒂费尔', c:'#1a237e'},
    {m:'Raymond James',   n:'雷蒙詹姆斯', c:'#003f87'},
    {m:'Oppenheimer',     n:'奥本海默', c:'#795548'},
    {m:'Cowen',           n:'考恩',     c:'#388e3c'},
    {m:'SoftBank',        n:'软银',     c:'#c0c0c0'},
    {m:'Macquarie',       n:'麦格理',   c:'#00263e'},
    {m:'Nomura',          n:'野村',     c:'#e4002b'},
    {m:'CLSA',            n:'里昂',     c:'#0066b3'},
    {m:'Evercore',        n:'艾弗考尔', c:'#2c3e50'},
    {m:'Canaccord',       n:'加通贝祥', c:'#1b5e20'},
  ];
  function _imeSource(item){
    var fn = (item.file_name||'').toLowerCase();
    var cat = (item.category||'').toLowerCase();
    // 先检查分类（如"1、彭博Bloomberg研报"）
    if(cat.indexOf('彭博')>=0 || cat.indexOf('bloomberg')>=0) return _imeSrcRules[8]; // 彭博
    // 再检查文件名前缀
    for(var i=0;i<_imeSrcRules.length;i++){
      if(fn.indexOf(_imeSrcRules[i].m.toLowerCase())===0) return _imeSrcRules[i];
    }
    // 文件名任意位置匹配（兜底）
    for(var i=0;i<_imeSrcRules.length;i++){
      if(fn.indexOf(_imeSrcRules[i].m.toLowerCase())>=0) return _imeSrcRules[i];
    }
    return null;
  }
  var _imeSrcDefault = {m:'', n:'外资', c:'#78909c'};
  function _imeSrcBadge(item){
    if(item._type !== 'report') return '';
    var s = _imeSource(item) || _imeSrcDefault;
    return '<span class="ime2-src-tag" style="background:'+s.c+'">'+s.n+'</span>';
  }

  function _cleanMd(s){ return (s||'').replace(/\*\*/g,'').replace(/\*/g,''); }
  function _trunc(s, n){ return (s && s.length > n) ? s.slice(0, n)+'…' : (s||''); }
  function _esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function _fmtNum(n){
    n = Number(n || 0);
    if(n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + '万';
    return String(n);
  }
  function _fmtTime(raw){
    if(!raw) return '';
    var s = raw.replace('T',' ').replace(/\+.*$/,'');
    var m = s.match(/(\d{4})-(\d{2})-(\d{2})\s*(\d{2}:\d{2})?/);
    if(!m) return s.slice(0,16);
    var y=m[1], mo=parseInt(m[2],10), d=parseInt(m[3],10), hm=m[4]||'';
    var now=new Date(), today=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0')+'-'+String(now.getDate()).padStart(2,'0');
    var ds=m[1]+'-'+m[2]+'-'+m[3];
    var prefix='';
    if(ds===today) prefix='今天';
    else{
      var yd=new Date(now); yd.setDate(yd.getDate()-1);
      var yds=yd.getFullYear()+'-'+String(yd.getMonth()+1).padStart(2,'0')+'-'+String(yd.getDate()).padStart(2,'0');
      if(ds===yds) prefix='昨天';
    }
    if(prefix) return prefix+(hm?' '+hm:'');
    if(y===String(now.getFullYear())) return mo+'月'+d+'日'+(hm?' '+hm:'');
    return y+'年'+mo+'月'+d+'日'+(hm?' '+hm:'');
  }

  window.fetchImeReports = function(){};  // backward compat stub
  window._imeHtFilter    = function(){};  // backward compat stub

