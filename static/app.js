const $ = (selector) => document.querySelector(selector);

const createSpan = (text) => {
  const el = document.createElement('span');
  el.style.display = 'block';
  el.textContent = text;
  return el;
};

const BIN_CLASS_MAP = {
  '#36a673': 'green',
  '#f0ad35': 'amber',
  '#d93e47': 'red',
  '#aa33b4': 'violet',
};

function renderBins(bins) {
  const rail = $('#binRail');
  // 移除旧的分桶卡片，保留合计行
  rail.querySelectorAll('.bin:not(.total)').forEach((el) => el.remove());
  bins.forEach((bin) => {
    const card = document.createElement('article');
    card.className = `bin ${BIN_CLASS_MAP[bin.color] || ''}`;
    const label = document.createElement('span');
    label.textContent = bin.label;
    const value = document.createElement('strong');
    value.textContent = bin.count.toLocaleString('zh-CN');
    card.append(label, value);
    rail.insertBefore(card, rail.lastElementChild);
  });

  // 动态渲染图例（保留统计边界标识）
  const legend = $('#legend');
  legend.querySelectorAll('span:not(.boundary)').forEach((el) => el.remove());
  // 标记统计边界为 boundary 类以便保留
  const boundary = legend.querySelector('span');
  if (boundary) boundary.classList.add('boundary');
  bins.slice().reverse().forEach((bin) => {
    const item = document.createElement('span');
    const swatch = document.createElement('i');
    swatch.className = BIN_CLASS_MAP[bin.color] || '';
    item.append(swatch, bin.label.replace(' μm', ''));
    legend.insertBefore(item, legend.firstElementChild);
  });
}

const imageInput = $('#imageInput');
const dropZone = $('#dropZone');
const fileName = $('#fileName');
const analyzeButton = $('#analyzeButton');
const previewImage = $('#previewImage');
const imageFrame = $('#imageFrame');
const emptyState = $('#emptyState');
const regionOverlay = $('#regionOverlay');
const processing = $('#processing');
const results = $('#results');
const errorMessage = $('#errorMessage');

let selectedFile = null;
let sourceUrl = null;
let showingResult = false;

const parameterIds = [
  'scaleUm', 'scalePx', 'centerX', 'centerY', 'radiusX', 'radiusY',
  'edgeThreshold', 'seedThreshold', 'guardUm'
];
const defaultParameters = Object.fromEntries(parameterIds.map((id) => [id, $(`#${id}`).value]));
const templateStorageKey = 'particle-counter-parameter-templates-v1';

const regionControls = [
  ['centerX', 'cxOut'], ['centerY', 'cyOut'],
  ['radiusX', 'rxOut'], ['radiusY', 'ryOut']
];

function updateRegion() {
  const cx = Number($('#centerX').value);
  const cy = Number($('#centerY').value);
  const rx = Number($('#radiusX').value);
  const ry = Number($('#radiusY').value);
  regionOverlay.style.left = `${cx - rx}%`;
  regionOverlay.style.top = `${cy - ry}%`;
  regionOverlay.style.width = `${rx * 2}%`;
  regionOverlay.style.height = `${ry * 2}%`;
  regionControls.forEach(([inputId, outputId]) => {
    $(`#${outputId}`).textContent = `${Number($(`#${inputId}`).value).toFixed(1).replace('.0', '')}%`;
  });
}

function markParametersDirty() {
  if (!selectedFile) return;
  if (showingResult) previewImage.src = sourceUrl;
  showingResult = false;
  regionOverlay.classList.remove('hidden');
  results.classList.add('hidden');
}

parameterIds.forEach((inputId) => $(`#${inputId}`).addEventListener('input', () => {
  updateRegion();
  markParametersDirty();
}));

function readTemplateStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(templateStorageKey) || '{}');
    if (parsed.version === 1 && Array.isArray(parsed.templates)) return parsed;
  } catch (_) {
    // A damaged local preference should never prevent image analysis.
  }
  return { version: 1, templates: [] };
}

function writeTemplateStore(store) {
  localStorage.setItem(templateStorageKey, JSON.stringify(store));
}

function currentParameters() {
  return Object.fromEntries(parameterIds.map((id) => [id, $(`#${id}`).value]));
}

function applyParameters(values) {
  parameterIds.forEach((id) => {
    if (Object.hasOwn(values, id)) $(`#${id}`).value = values[id];
  });
  updateRegion();
  markParametersDirty();
}

function renderTemplates(selectedId = '') {
  const select = $('#templateSelect');
  const store = readTemplateStore();
  select.replaceChildren(new Option('默认参数', ''));
  store.templates.forEach((template) => select.add(new Option(template.name, template.id)));
  select.value = store.templates.some((item) => item.id === selectedId) ? selectedId : '';
  $('#deleteTemplate').disabled = !select.value;
}

function showTemplateMessage(message, isError = false) {
  const element = $('#templateMessage');
  element.textContent = message;
  element.classList.toggle('error', isError);
}

$('#templateSelect').addEventListener('change', (event) => {
  const selectedId = event.target.value;
  const template = readTemplateStore().templates.find((item) => item.id === selectedId);
  applyParameters(template ? template.values : defaultParameters);
  $('#deleteTemplate').disabled = !selectedId;
  $('#templateName').value = template ? template.name : '';
  showTemplateMessage(template ? `已应用“${template.name}”。` : '已恢复默认参数。');
});

$('#saveTemplate').addEventListener('click', () => {
  const name = $('#templateName').value.trim();
  if (!name) {
    showTemplateMessage('请先填写模板名称。', true);
    $('#templateName').focus();
    return;
  }
  const store = readTemplateStore();
  let template = store.templates.find((item) => item.name.toLocaleLowerCase('zh-CN') === name.toLocaleLowerCase('zh-CN'));
  if (template) {
    template.name = name;
    template.values = currentParameters();
  } else {
    template = {
      id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      name,
      values: currentParameters()
    };
    store.templates.push(template);
  }
  writeTemplateStore(store);
  renderTemplates(template.id);
  showTemplateMessage(`已保存“${name}”。`);
});

$('#deleteTemplate').addEventListener('click', () => {
  const selectedId = $('#templateSelect').value;
  if (!selectedId) return;
  const store = readTemplateStore();
  const template = store.templates.find((item) => item.id === selectedId);
  store.templates = store.templates.filter((item) => item.id !== selectedId);
  writeTemplateStore(store);
  renderTemplates();
  applyParameters(defaultParameters);
  $('#templateName').value = '';
  showTemplateMessage(template ? `已删除“${template.name}”，并恢复默认参数。` : '模板已删除，并恢复默认参数。');
});

function setFile(file) {
  if (!file || !file.type.startsWith('image/')) {
    errorMessage.textContent = '请选择图片文件。';
    return;
  }
  selectedFile = file;
  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  sourceUrl = URL.createObjectURL(file);
  previewImage.src = sourceUrl;
  fileName.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
  emptyState.classList.add('hidden');
  imageFrame.classList.remove('hidden');
  regionOverlay.classList.remove('hidden');
  results.classList.add('hidden');
  showingResult = false;
  analyzeButton.disabled = false;
  errorMessage.textContent = '';
  updateRegion();
}

imageInput.addEventListener('change', () => setFile(imageInput.files[0]));
['dragenter', 'dragover'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.add('dragging');
}));
['dragleave', 'drop'].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.remove('dragging');
}));
dropZone.addEventListener('drop', (event) => setFile(event.dataTransfer.files[0]));

function appendField(form, name, selector) {
  form.append(name, $(selector).value);
}

analyzeButton.addEventListener('click', async () => {
  if (!selectedFile) return;
  errorMessage.textContent = '';
  analyzeButton.disabled = true;
  processing.classList.remove('hidden');
  results.classList.add('hidden');

  const form = new FormData();
  form.append('image', selectedFile);
  appendField(form, 'scale_um', '#scaleUm');
  appendField(form, 'scale_px', '#scalePx');
  appendField(form, 'center_x', '#centerX');
  appendField(form, 'center_y', '#centerY');
  appendField(form, 'radius_x', '#radiusX');
  appendField(form, 'radius_y', '#radiusY');
  appendField(form, 'edge_threshold', '#edgeThreshold');
  appendField(form, 'seed_threshold', '#seedThreshold');
  appendField(form, 'guard_um', '#guardUm');

  try {
    const response = await fetch('/api/analyze', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '分析失败。');

    previewImage.src = `${data.files.preview}?t=${Date.now()}`;
    showingResult = true;
    regionOverlay.classList.add('hidden');
    renderBins(data.bins);
    $('#countTotal').textContent = data.total.toLocaleString('zh-CN');
    $('#calibrationReadout').replaceChildren(
      createSpan(`${data.scale_px} px = ${data.scale_um} μm`),
      createSpan(`1 px = ${data.um_per_px} μm`),
    );
    $('#downloadBundle').href = data.files.bundle;
    $('#downloadAnnotated').href = data.files.annotated;
    $('#downloadSummary').href = data.files.summary;
    $('#downloadMeasurements').href = data.files.measurements;
    results.classList.remove('hidden');
    results.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    errorMessage.textContent = error.message;
  } finally {
    processing.classList.add('hidden');
    analyzeButton.disabled = false;
  }
});

previewImage.addEventListener('load', updateRegion);
renderTemplates();
updateRegion();
