// ============ 字体加载检测 ============
// 检测字体加载完成，避免图标文字闪烁
(function () {
  // 使用 Promise.race 实现超时保护
  const fontLoadTimeout = new Promise(resolve => {
    setTimeout(() => {
      console.warn('字体加载超时，强制显示图标');
      resolve('timeout');
    }, 3000);
  });

  const fontLoadReady = document.fonts.ready.then(() => 'loaded');

  Promise.race([fontLoadReady, fontLoadTimeout]).then((result) => {
    document.body.classList.add('fonts-loaded');
    if (result === 'loaded') {
      console.log('Material Icons 字体加载完成');
    }
  }).catch((error) => {
    console.error('字体加载检测失败:', error);
    // 出错时也显示图标，避免永久隐藏
    document.body.classList.add('fonts-loaded');
  });
})();

$(function () {
  // 左侧分区导航：滚动定位并高亮当前分区
  $(".side-nav a").on("click", function (e) {
    e.preventDefault();
    const target = $(this).data("target");
    $(".side-nav a").removeClass("on");
    $(this).addClass("on");
    if (target) {
      const el = document.getElementById(target);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  // 遍历所有的select元素，默认选中只有1个选项的
  const autoSelectOne = () => {
    $("select").each(function () {
      // 如果select元素仅有一个option子元素
      if ($(this).children("option").length === 1) {
        // 选中这个option
        $(this).find("option").prop("selected", true);
      }
    });
  };

  function updateCheckbox(selector, mi_did, device_list, accountPassValid) {
    // 清除现有的内容
    $(selector).empty();

    // 将 mi_did 字符串通过逗号分割转换为数组，以便于判断默认选中项
    var selected_dids = mi_did.split(",");

    //如果device_list为空，则可能是未设置小米账号密码或者已设置密码，但是没有过小米验证，此处需要提示用户
    if (device_list.length == 0) {
      const loginTips = accountPassValid
        ? `<div class="login-tips">未发现可用的小爱设备，请检查账号密码是否输错，并关闭加速代理或在<a href="https://www.mi.com">小米官网</a>登陆过人脸或滑块验证。如仍未解决。请根据<a href="https://github.com/hanxi/xiaomusic/issues/99">FAQ</a>的内容解决问题。</div>`
        : `<div class="login-tips">未发现可用的小爱设备，请先在下面的输入框中设置小米的<b>账号和密码</b></div>`;
      $(selector).append(loginTips);
      return;
    }
    $.each(device_list, function (index, device) {
      var did = device.miotDID;
      var hardware = device.hardware;
      var name = device.name;
      // 创建复选框元素
      var checkbox = $("<input>", {
        type: "checkbox",
        id: did,
        value: `${did}`,
        class: "custom-checkbox", // 添加样式类
        // 如果mi_did中包含了该did，则默认选中
        checked: selected_dids.indexOf(did) !== -1,
      });

      // 创建标签元素
      var label = $("<label>", {
        for: did,
        class: "checkbox-label", // 添加样式类
        text: `【${hardware} ${did}】${name}`, // 设定标签内容
      });

      // 将复选框和标签添加到目标选择器元素中
      $(selector).append(checkbox).append(label);
    });
  }

  function getSelectedDids(containerSelector) {
    var selectedDids = [];

    // 仅选择给定容器中选中的复选框
    $(containerSelector + " .custom-checkbox:checked").each(function () {
      var did = this.value;
      selectedDids.push(did);
    });

    return selectedDids.join(",");
  }

  // 获取设备列表（供“获取设备列表”按钮和初始加载共用）
  function fetchDeviceList(callback) {
    $.get("/getsetting?need_device_list=true", function (data, status) {
      if (typeof callback === "function") {
        callback(data, status);
      }
    }).fail(function (xhr) {
      alert(
        "获取设备列表失败: " +
          (xhr.responseJSON && xhr.responseJSON.detail
            ? xhr.responseJSON.detail
            : xhr.statusText)
      );
    });
  }

  // 初始加载：拉取配置并填充表单与设备列表
  fetchDeviceList(function (data, status) {
    console.log(data, status);
    var accountPassValid = data.account && data.password;
    updateCheckbox("#mi_did", data.mi_did || "", data.device_list || [], accountPassValid);

    // 处理hostname和public_port，拆分到新的字段
    if (data.hostname) {
      const urlParts = data.hostname.match(/^(https?:\/\/)?([^:\/]+)(:\d+)?/);
      if (urlParts) {
        const protocol = urlParts[1] ? urlParts[1].replace("://", "") : "http";
        const host = urlParts[2];
        $("#protocol").val(protocol);
        $("#host").val(host);
      }
    }
    // 强制使用public_port字段的值，如果不存在则使用默认值
    $("#port").val(data.public_port || "8090");

    // 处理Emby服务器地址，拆分到新的字段
    if (data.emby_host) {
      const urlParts = data.emby_host.match(/^(https?:\/\/)?([^:\/]+)(:\d+)?/);
      if (urlParts) {
        const protocol = urlParts[1] ? urlParts[1].replace("://", "") : "http";
        const host = urlParts[2];
        const port = urlParts[3] ? urlParts[3].replace(":", "") : "8096";
        $("#emby_protocol").val(protocol);
        $("#emby_host_input").val(host);
        $("#emby_port").val(port);
      }
    }

    // 处理Emby多用户配置
    if (data.emby_users && typeof data.emby_users === 'object') {
      renderEmbyUsers(data.emby_users);
    } else {
      renderEmbyUsers({});
    }

    // 处理其他配置项
    for (const key in data) {
      if (key === "hostname" || key === "public_port" || key === "port" || key === "emby_host" || key === "emby_users") continue; // 跳过已经处理的字段
      const $element = $("#" + key);
      if ($element.length) {
        if (data[key] === true) {
          $element.val("true");
        } else if (data[key] === false) {
          $element.val("false");
        } else {
          $element.val(data[key]);
        }
      }
    }

    autoSelectOne();

    renderCronRules(data.crontab_json || "[]");
    populateShutdownDevices();
  });

    $("#update-devices").on("click", function () {
    var $btn = $(this);
    var oldText = $btn.text();
    $btn.prop("disabled", true).text("更新中…");
    $.get("/device_list")
      .done(function (data) {
        var currentMiDid = getSelectedDids("#mi_did");
        var raw = data.devices || {};
        var deviceList = Object.keys(raw).map(function (did) {
          var d = raw[did];
          return { miotDID: d.did || did, hardware: d.hardware || "", name: d.name || "" };
        });
        updateCheckbox("#mi_did", currentMiDid, deviceList);
      })
      .fail(function (xhr) {
        alert("更新设备列表失败: " + (xhr.responseJSON && xhr.responseJSON.detail ? xhr.responseJSON.detail : xhr.statusText));
      })
      .always(function () {
        $btn.prop("disabled", false).text(oldText);
      });
  });


  $(".save-button").on("click", () => {
    var setting = $("#setting");
    var inputs = setting.find("input, select, textarea");
    var data = {};
    var specialFields = {};
    var embySpecialFields = {};
    inputs.each(function () {
      var id = this.id;
      if (id) {
        if (id === "protocol" || id === "host" || id === "port") {
          specialFields[id] = $(this).val();
        } else if (id === "emby_protocol" || id === "emby_host_input" || id === "emby_port") {
          embySpecialFields[id] = $(this).val();
        } else {
          data[id] = $(this).val();
        }
      }
    });
    
    // 合并特殊字段为hostname和public_port
    const protocol = specialFields.protocol || "http";
    const host = specialFields.host || "localhost";
    const port = specialFields.port || "7777";
    data["hostname"] = `${protocol}://${host}`;
    data["public_port"] = port;
    
    // 合并Emby特殊字段为emby_host
    const embyProtocol = embySpecialFields.emby_protocol || "http";
    const embyHost = embySpecialFields.emby_host_input || "127.0.0.1";
    const embyPort = embySpecialFields.emby_port || "48096";
    data["emby_host"] = `${embyProtocol}://${embyHost}:${embyPort}`;
    
    // 添加Emby多用户配置
    data["emby_users"] = getEmbyUsersConfig();
    
    var did_list = getSelectedDids("#mi_did");
    data["mi_did"] = did_list;
    console.log(data);

    $.ajax({
      type: "POST",
      url: "/savesetting",
      contentType: "application/json",
      data: JSON.stringify(data),
      success: (msg) => {
        alert(msg);
        location.reload();
      },
      error: (msg) => {
        alert(msg);
      },
    });
  });

  $("#clear_cache").on("click", () => {
    localStorage.clear();
    alert("清除成功");
  });

  $("#cleantempdir").on("click", () => {
    $.ajax({
      type: "POST",
      url: "/xiaoemby/file/cleantempdir",
      contentType: "application/json",
      data: JSON.stringify({}),
      success: (msg) => {
        alert(msg.ret);
      },
      error: (msg) => {
        alert(msg);
      },
    });
  });



  // 高级配置折叠功能
  const toggleBtn = $("#advancedConfigToggle");
  const content = $("#advancedConfigContent");

  // 从localStorage读取折叠状态，默认折叠
  const isCollapsed =
    localStorage.getItem("advancedConfigCollapsed") !== "false";

  // 初始化状态
  if (isCollapsed) {
    toggleBtn.addClass("collapsed");
    content.addClass("collapsed");
  }

  // 点击切换折叠状态
  toggleBtn.on("click", function () {
    const willCollapse = !toggleBtn.hasClass("collapsed");

    if (willCollapse) {
      toggleBtn.addClass("collapsed");
      content.addClass("collapsed");
      localStorage.setItem("advancedConfigCollapsed", "true");
    } else {
      toggleBtn.removeClass("collapsed");
      content.removeClass("collapsed");
      localStorage.setItem("advancedConfigCollapsed", "false");
    }
  });

  // 高级配置 Tab 切换：委托到高级配置容器，阻止冒泡并强制切换面板
  $("#advancedConfigContent").on("click", ".config-tab-button", function (e) {
    e.preventDefault();
    e.stopPropagation();

    var $btn = $(this);
    var panelId = $btn.attr("aria-controls") || ("tab-" + $btn.data("tab"));
    if (!panelId) return;

    $("#advancedConfigContent .config-tab-button").removeClass("active").attr("aria-selected", "false");
    $btn.addClass("active").attr("aria-selected", "true");

    $("#advancedConfigContent .config-tab-content").removeClass("active");
    var $panel = $("#" + panelId);
    if ($panel.length) {
      $panel.addClass("active");
    }
  });

  // 功能操作区域折叠功能
  const operationToggle = $("#operationToggle");
  const operationContent = $("#operationContent");

  // 从localStorage读取折叠状态，默认折叠
  const operationCollapsedState = localStorage.getItem("operationCollapsed");
  const isOperationCollapsed =
    operationCollapsedState === null || operationCollapsedState === "true";

  // 初始化状态
  if (!isOperationCollapsed) {
    // 如果用户之前展开过，则移除 collapsed 类
    operationToggle.removeClass("collapsed");
    operationContent.removeClass("collapsed");
  }

  // 点击切换折叠状态
  operationToggle.on("click", function () {
    const willCollapse = !operationToggle.hasClass("collapsed");

    if (willCollapse) {
      operationToggle.addClass("collapsed");
      operationContent.addClass("collapsed");
      localStorage.setItem("operationCollapsed", "true");
    } else {
      operationToggle.removeClass("collapsed");
      operationContent.removeClass("collapsed");
      localStorage.setItem("operationCollapsed", "false");
    }
  });

  // 工具链接区域折叠功能
  const toolsToggle = $("#toolsToggle");
  const toolsContent = $("#toolsContent");

  // 从localStorage读取折叠状态，默认折叠
  const toolsCollapsedState = localStorage.getItem("toolsCollapsed");
  const isToolsCollapsed =
    toolsCollapsedState === null || toolsCollapsedState === "true";

  // 初始化状态
  if (!isToolsCollapsed) {
    // 如果用户之前展开过，则移除 collapsed 类
    toolsToggle.removeClass("collapsed");
    toolsContent.removeClass("collapsed");
  }

  // 点击切换折叠状态
  toolsToggle.on("click", function () {
    const willCollapse = !toolsToggle.hasClass("collapsed");

    if (willCollapse) {
      toolsToggle.addClass("collapsed");
      toolsContent.addClass("collapsed");
      localStorage.setItem("toolsCollapsed", "true");
    } else {
      toolsToggle.removeClass("collapsed");
      toolsContent.removeClass("collapsed");
      localStorage.setItem("toolsCollapsed", "false");
    }
  });

  // ============ 无障碍功能 ============

  // 高级配置折叠按钮的 ARIA 更新
  function updateAdvancedConfigAria() {
    const isExpanded = !toggleBtn.hasClass("collapsed");
    toggleBtn.attr("aria-expanded", isExpanded ? "true" : "false");
  }

  // 初始化 ARIA 状态
  updateAdvancedConfigAria();

  // 修改高级配置折叠点击事件，添加 ARIA 更新
  toggleBtn.off("click").on("click", function () {
    const willCollapse = !toggleBtn.hasClass("collapsed");

    if (willCollapse) {
      toggleBtn.addClass("collapsed");
      content.addClass("collapsed");
      localStorage.setItem("advancedCollapsed", "true");
    } else {
      toggleBtn.removeClass("collapsed");
      content.removeClass("collapsed");
      localStorage.setItem("advancedCollapsed", "false");
    }

    // 更新 ARIA 属性
    updateAdvancedConfigAria();
  });

  // 高级配置折叠按钮的键盘支持
  toggleBtn.on("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      $(this).click();
      e.preventDefault();
    }
  });

  // 工具折叠按钮的 ARIA 更新
  function updateToolsAria() {
    const isExpanded = !toolsToggle.hasClass("collapsed");
    toolsToggle.attr("aria-expanded", isExpanded ? "true" : "false");
  }

  // 初始化工具折叠的 ARIA 状态
  if (typeof toolsToggle !== "undefined" && toolsToggle.length > 0) {
    updateToolsAria();

    // 修改工具折叠点击事件，添加 ARIA 更新
    toolsToggle.off("click").on("click", function () {
      const willCollapse = !toolsToggle.hasClass("collapsed");

      if (willCollapse) {
        toolsToggle.addClass("collapsed");
        toolsContent.addClass("collapsed");
        localStorage.setItem("toolsCollapsed", "true");
      } else {
        toolsToggle.removeClass("collapsed");
        toolsContent.removeClass("collapsed");
        localStorage.setItem("toolsCollapsed", "false");
      }

      // 更新 ARIA 属性
      updateToolsAria();
    });

    // 工具折叠按钮的键盘支持
    toolsToggle.on("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        $(this).click();
        e.preventDefault();
      }
    });
  }

  // 为所有自定义按钮添加键盘支持
  $('[role="button"]').on("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      $(this).click();
      e.preventDefault();
    }
  });

  // ============ Emby多用户配置功能 ============
  function renderEmbyUsers(embyUsers) {
    const container = $("#emby_users_container");
    container.empty();
    
    if (!embyUsers || Object.keys(embyUsers).length === 0) {
      container.append('<p style="color: #666;">暂无用户配置，点击下方"添加用户"按钮添加</p>');
      return;
    }
    
    Object.keys(embyUsers).forEach(function(key) {
      const user = embyUsers[key];
      addEmbyUserRow(user.user_id, user.alias, user.is_default);
    });
  }

  function addEmbyUserRow(userId = "", alias = "", isDefault = false) {
    const container = $("#emby_users_container");
    const index = container.children('.emby-user-row').length;
    
    const userRow = $('<div>', {
      class: 'emby-user-row',
      style: 'display: flex; gap: 10px; margin-bottom: 10px; align-items: center;'
    });
    
    const userIdInput = $('<input>', {
      type: 'text',
      class: 'emby-user-id',
      placeholder: '用户ID',
      value: userId,
      style: 'flex: 2;'
    });
    
    const aliasInput = $('<input>', {
      type: 'text',
      class: 'emby-user-alias',
      placeholder: '别名（如：爸爸、妈妈）',
      value: alias,
      style: 'flex: 2;'
    });
    
    const defaultCheckbox = $('<input>', {
      type: 'checkbox',
      class: 'emby-user-default',
      checked: isDefault
    });
    
    const defaultLabel = $('<label>', {
      style: 'display: flex; align-items: center; gap: 5px; white-space: nowrap;'
    }).append(defaultCheckbox).append('默认用户');
    
    const deleteBtn = $('<button>', {
      type: 'button',
      class: 'mini-button',
      style: 'background: #f44336; color: white;',
      text: '删除'
    }).on('click', function() {
      userRow.remove();
      // 如果删除的是默认用户，自动设置第一个为默认
      if (isDefault) {
        const firstCheckbox = container.find('.emby-user-default').first();
        if (firstCheckbox.length) {
          firstCheckbox.prop('checked', true);
        }
      }
    });
    
    // 确保只有一个默认用户
    defaultCheckbox.on('change', function() {
      if ($(this).prop('checked')) {
        container.find('.emby-user-default').not(this).prop('checked', false);
      }
    });
    
    userRow.append(userIdInput).append(aliasInput).append(defaultLabel).append(deleteBtn);
    container.append(userRow);
  }

  function getEmbyUsersConfig() {
    const embyUsers = {};
    let index = 0;
    
    $("#emby_users_container .emby-user-row").each(function() {
      const userId = $(this).find('.emby-user-id').val().trim();
      const alias = $(this).find('.emby-user-alias').val().trim();
      const isDefault = $(this).find('.emby-user-default').prop('checked');
      
      if (userId && alias) {
        const key = `user_${index}`;
        embyUsers[key] = {
          user_id: userId,
          alias: alias,
          is_default: isDefault
        };
        index++;
      }
    });
    
    return embyUsers;
  }

  // 绑定添加用户按钮事件
  $("#add_emby_user").on("click", function() {
    addEmbyUserRow();
  });

  // 将函数暴露到全局作用域，以便在保存配置时使用
  window.getEmbyUsersConfig = getEmbyUsersConfig;

  // Emby 登录测试（不保存配置）
  $("#emby_test_login").on("click", function () {
    const $btn = $(this);
    const $result = $("#emby_test_result");
    const protocol = $("#emby_protocol").val() || "http";
    const host = $("#emby_host_input").val() || "";
    const port = $("#emby_port").val() || "";
    const fullHost = `${protocol}://${host}${port ? ":" + port : ""}`;
    $result.removeClass("ok err").text("正在测试…");
    $btn.prop("disabled", true);
    $.ajax({
      type: "POST",
      url: "/xiaoemby/system/test_emby_login",
      contentType: "application/json",
      data: JSON.stringify({
        host: fullHost,
        username: $("#emby_username").val(),
        password: $("#emby_password").val(),
      }),
      success: function (resp) {
        $result
          .removeClass("ok err")
          .addClass(resp.success ? "ok" : "err")
          .text(resp.message || (resp.success ? "登录成功" : "登录失败"));
      },
      error: function (xhr) {
        $result.removeClass("ok err").addClass("err").text("请求失败：" + xhr.status);
      },
      complete: function () {
        $btn.prop("disabled", false);
      },
    });
  });

  // ============ 本地曲库镜像同步 ============
  $("[data-mirror-sync]").on("click", function () {
    const $btn = $(this);
    const $result = $("#mirror_sync_result");
    const kind = $btn.data("kind") || "full";
    $result.removeClass("ok err").text("正在同步本地曲库…");
    $btn.prop("disabled", true);
    $.ajax({
      type: "POST",
      url: "/xiaoemby/system/sync_mirror",
      contentType: "application/json",
      data: JSON.stringify({ kind: kind }),
      success: function (resp) {
        let detail;
        if (resp && resp.skipped) {
          detail = "未启用：" + (resp.reason || "未配置镜像路径");
        } else if (resp && resp.ok) {
          detail = "完成（" + (resp.kind || kind) + "）：v" + (resp.version || 0) +
            "，共 " + (resp.tracks || 0) + " 首";
        } else {
          detail = "失败：" + ((resp && resp.reason) || "未知错误");
        }
        $result.removeClass("ok err").addClass(resp && resp.ok ? "ok" : "err").text(detail);
      },
      error: function (xhr) {
        $result.removeClass("ok err").addClass("err").text("请求失败：" + xhr.status);
      },
      complete: function () {
        $btn.prop("disabled", false);
      },
    });
  });

  // ============ 定时开关语音口令 ============
  const CRON_REPEAT = [
    ["every", "每天", "*"],
    ["weekday", "工作日", "1-5"],
    ["monday", "每周一", "1"],
    ["friday", "每周五", "5"],
    ["weekend", "周末", "0,6"],
  ];
  const repeatLabel = (r) => {
    const hit = CRON_REPEAT.find((x) => x[0] === r);
    return hit ? hit[1] : "自定义";
  };
  const cronToUI = (expr) => {
    const p = (expr || "").trim().split(/\s+/);
    if (p.length !== 5) return { time: "07:30", repeat: "every" };
    let repeat = "custom";
    if (p[2] === "*" && p[3] === "*") {
      const hit = CRON_REPEAT.find((x) => x[2] === p[4]);
      if (hit) repeat = hit[0];
    }
    return {
      time: String(p[1]).padStart(2, "0") + ":" + String(p[0]).padStart(2, "0"),
      repeat: repeat,
    };
  };
  const uiToCron = (time, repeat) => {
    const parts = (time || "07:30").split(":");
    const h = Number(parts[0]), m = Number(parts[1]);
    const hit = CRON_REPEAT.find((x) => x[0] === repeat);
    const dow = hit ? hit[2] : "*";
    return `${m} ${h} * * ${dow}`;
  };
  const cronDeviceOptions = (selected) => {
    let html = '<option value="">全部设备</option>';
    $("#mi_did input.custom-checkbox").each(function () {
      const did = $(this).val();
      const label = $(`label[for="${did}"]`).text() || did;
      html += `<option value="${did}" ${did === selected ? "selected" : ""}>${label}</option>`;
    });
    return html;
  };
  const cronDeviceLabel = (did) => {
    if (!did) return "全部设备";
    const el = $(`#mi_did input.custom-checkbox[value="${did}"]`);
    return el.length ? ($(`label[for="${did}"]`).text() || did) : did;
  };

  let cronRules = [];
  let cronOtherJobs = [];

  function ruleFromJob(job) {
    const ui = cronToUI(job.expression);
    return {
      action: (job.arg1 || "").indexOf("disable") >= 0 ? "off" : "on",
      time: ui.time,
      repeat: ui.repeat,
      did: job.did || "",
      enabled: job.enabled !== false,
    };
  }
  function jobFromRule(r) {
    return {
      name: "set_pull_ask",
      expression: uiToCron(r.time, r.repeat),
      did: r.did,
      arg1: "enable_pull_ask|" + (r.action === "on" ? "enable" : "disable"),
      enabled: r.enabled,
    };
  }
  function syncCronJson() {
    const jobs = cronRules.map(jobFromRule).concat(cronOtherJobs);
    $("#crontab_json").val(JSON.stringify(jobs));
  }
  function drawCronRules() {
    const box = $("#cron_rules");
    box.empty();
    if (!cronRules.length) {
      box.append('<div class="rule-empty">暂无规则，点击「新建规则」添加</div>');
    }
    cronRules.forEach((r, i) => {
      const repOpts = CRON_REPEAT.map(
        (x) => `<option value="${x[0]}" ${x[0] === r.repeat ? "selected" : ""}>${x[1]}</option>`
      ).join("") + `<option value="custom" ${r.repeat === "custom" ? "selected" : ""}>自定义</option>`;
      const actOpts = `<option value="on" ${r.action === "on" ? "selected" : ""}>开启语音口令</option><option value="off" ${r.action === "off" ? "selected" : ""}>关闭语音口令</option>`;
      box.append(`
        <div class="rule" data-i="${i}">
          <div class="rule-head">
            <div class="act ${r.action}">${r.action === "on" ? "开启" : "关闭"}</div>
            <div class="rule-main">
              <div class="rule-title">${repeatLabel(r.repeat)} ${r.time} · ${r.action === "on" ? "开启" : "关闭"}语音口令</div>
              <div class="rule-meta">${cronDeviceLabel(r.did)} <span class="badge ${r.enabled ? "on" : "off"}" style="margin-left:6px">${r.enabled ? "已启用" : "已停用"}</span></div>
            </div>
            <div class="switch ${r.enabled ? "on" : ""}" data-cron-tog="${i}"></div>
            <button type="button" class="btn sm" data-cron-edit="${i}">编辑</button>
          </div>
          <div class="rule-body">
            <div class="grid3">
              <div class="field"><label>动作</label><select class="cr-action">${actOpts}</select></div>
              <div class="field"><label>触发时间</label><input type="time" class="cr-time" value="${r.time}"></div>
              <div class="field"><label>重复</label><select class="cr-repeat">${repOpts}</select></div>
            </div>
            <div style="margin-top:12px"><div class="field"><label>目标设备</label><select class="cr-did">${cronDeviceOptions(r.did)}</select></div></div>
            <div style="margin-top:14px;display:flex;gap:8px">
              <button type="button" class="btn pri sm" data-cron-save="${i}">保存规则</button>
              <button type="button" class="btn sm" data-cron-del="${i}">删除</button>
            </div>
          </div>
        </div>`);
    });
    $("#cron_count").text("共 " + cronRules.length + " 条");
    box.find("[data-cron-edit]").on("click", function () {
      $(this).closest(".rule").toggleClass("open");
    });
    box.find("[data-cron-tog]").on("click", function () {
      const i = Number($(this).data("cron-tog"));
      cronRules[i].enabled = !cronRules[i].enabled;
      $(this).toggleClass("on");
      drawCronRules();
    });
    box.find("[data-cron-save]").on("click", function () {
      const row = $(this).closest(".rule");
      const i = Number($(this).data("cron-save"));
      cronRules[i] = {
        action: row.find(".cr-action").val(),
        time: row.find(".cr-time").val() || "07:30",
        repeat: row.find(".cr-repeat").val(),
        did: row.find(".cr-did").val(),
        enabled: cronRules[i].enabled,
      };
      syncCronJson();
      drawCronRules();
    });
    box.find("[data-cron-del]").on("click", function () {
      cronRules.splice(Number($(this).data("cron-del")), 1);
      syncCronJson();
      drawCronRules();
    });
    syncCronJson();
  }
  function renderCronRules(json) {
    let list = [];
    try { list = JSON.parse(json || "[]"); } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
    cronOtherJobs = list.filter((j) => j.name !== "set_pull_ask");
    cronRules = list.filter((j) => j.name === "set_pull_ask").map(ruleFromJob);
    drawCronRules();
  }
  $("#cron_add").on("click", function () {
    cronRules.push({ action: "on", time: "07:30", repeat: "every", did: "", enabled: true });
    syncCronJson();
    drawCronRules();
    $("#cron_rules .rule").last().addClass("open");
  });

  // 快捷定时关机：与主面板原来的“定时关机”能力一致，统一放到设置页
  function populateShutdownDevices() {
    const $sel = $("#shutdown_did");
    $sel.empty();
    $("#mi_did input.custom-checkbox").each(function () {
      const did = $(this).val();
      const label = $(`label[for="${did}"]`).text() || did;
      $sel.append(`<option value="${did}">${label}</option>`);
    });
    if (!$sel.children().length) {
      $sel.append('<option value="">无可用设备</option>');
    }
  }
  $("[data-shutdown]").on("click", function () {
    const did = $("#shutdown_did").val();
    const cmd = $(this).data("shutdown");
    const $result = $("#shutdown_result");
    if (!did) {
      $result.text("请先勾选设备并刷新设备列表");
      return;
    }
    $result.text("正在发送…");
    $.ajax({
      type: "POST",
      url: "/cmd",
      contentType: "application/json",
      data: JSON.stringify({ did: did, cmd: cmd }),
      success: function (resp) {
        $result.text(resp && resp.ret === "OK" ? `已发送：${cmd}` : `发送失败：${(resp && resp.ret) || "未知错误"}`);
      },
      error: function (xhr) {
        $result.text("请求失败：" + xhr.status);
      },
    });
  });
});
