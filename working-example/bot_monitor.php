<?php

declare(strict_types=1);

require_once __DIR__ . '/monitoring_lib.php';

requireAuth();

$user = currentUser();
$userId = (int)$user['id'];
$success = flash('success');
$error = flash('error');

function loadMonitorConnection(int $userId): ?array
{
    $stmt = db()->prepare('SELECT * FROM user_bot_monitors WHERE user_id = :user_id LIMIT 1');
    $stmt->execute(['user_id' => $userId]);
    $row = $stmt->fetch();

    return $row ?: null;
}

function saveMonitorStatus(int $userId, ?string $lastError): void
{
    $stmt = db()->prepare(
        'UPDATE user_bot_monitors
         SET last_sync_at = :last_sync_at, last_error = :last_error
         WHERE user_id = :user_id'
    );
    $stmt->execute([
        'user_id' => $userId,
        'last_sync_at' => date('Y-m-d H:i:s'),
        'last_error' => $lastError,
    ]);
}

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES, 'UTF-8');
}

function formatNumber($value, int $decimals = 2): string
{
    return number_format((float)$value, $decimals, '.', ' ');
}

function normalizeDateValue(?string $value, string $fallback): string
{
    if (!$value) {
        return $fallback;
    }

    $date = DateTimeImmutable::createFromFormat('Y-m-d', $value);
    return $date ? $date->format('Y-m-d') : $fallback;
}

function buildTradeChartData(array $trades, array $selectedBots, string $startDate, string $endDate): array
{
    $availableBots = [];

    foreach ($trades as $trade) {
        $symbol = trim((string)($trade['symbol'] ?? ''));

        if ($symbol !== '') {
            $availableBots[$symbol] = true;
        }
    }

    $availableBots = array_keys($availableBots);
    sort($availableBots, SORT_NATURAL);

    $selectedSet = [];

    foreach ($selectedBots as $bot) {
        if (in_array($bot, $availableBots, true)) {
            $selectedSet[$bot] = true;
        }
    }

    $useAllBots = $selectedSet === [];
    $start = new DateTimeImmutable($startDate);
    $end = new DateTimeImmutable($endDate);
    $days = [];

    for ($day = $start; $day <= $end; $day = $day->modify('+1 day')) {
        $days[$day->format('Y-m-d')] = [
            'date' => $day->format('Y-m-d'),
            'label' => $day->format('d.m'),
            'display_date' => $day->format('d.m.Y'),
            'pnl' => 0.0,
            'trades' => 0,
        ];
    }

    foreach ($trades as $trade) {
        $symbol = trim((string)($trade['symbol'] ?? ''));

        if (!$useAllBots && !isset($selectedSet[$symbol])) {
            continue;
        }

        $createdTime = (int)($trade['createdTime'] ?? 0);

        if ($createdTime <= 0) {
            continue;
        }

        $dayKey = (new DateTimeImmutable('@' . (int)floor($createdTime / 1000)))
            ->setTimezone(new DateTimeZone(date_default_timezone_get()))
            ->format('Y-m-d');

        if (!isset($days[$dayKey])) {
            continue;
        }

        $days[$dayKey]['pnl'] += (float)($trade['closedPnl'] ?? 0);
        $days[$dayKey]['trades']++;
    }

    $cumulative = 0.0;
    $points = [];

    foreach ($days as $day) {
        $cumulative += $day['pnl'];
        $points[] = [
            'date' => $day['date'],
            'label' => $day['label'],
            'display_date' => $day['display_date'],
            'day_pnl' => round($day['pnl'], 4),
            'pnl' => round($cumulative, 4),
            'trades' => $day['trades'],
        ];
    }

    return [
        'available_bots' => $availableBots,
        'selected_bots' => array_keys($selectedSet),
        'points' => $points,
    ];
}

function buildTradeSummary(array $points, float $currentWalletBalance): array
{
    $daysCount = max(1, count($points));
    $totalProfit = $points ? (float)$points[array_key_last($points)]['pnl'] : 0.0;
    $totalTrades = 0;

    foreach ($points as $point) {
        $totalTrades += (int)$point['trades'];
    }

    $estimatedStartBalance = $currentWalletBalance - $totalProfit;

    if ($estimatedStartBalance <= 0) {
        $estimatedStartBalance = $currentWalletBalance;
    }

    $periodReturn = $estimatedStartBalance > 0 ? ($totalProfit / $estimatedStartBalance) * 100 : 0.0;
    $annualReturn = $periodReturn * (365 / $daysCount);

    return [
        'total_profit' => $totalProfit,
        'total_trades' => $totalTrades,
        'avg_profit_day' => $totalProfit / $daysCount,
        'avg_trades_day' => $totalTrades / $daysCount,
        'period_return' => $periodReturn,
        'annual_return' => $annualReturn,
    ];
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    try {
        $label = trim((string)($_POST['label'] ?? ''));
        $apiKey = trim((string)($_POST['api_key'] ?? ''));
        $apiSecret = trim((string)($_POST['api_secret'] ?? ''));

        if ($label === '' || $apiKey === '' || $apiSecret === '') {
            throw new RuntimeException('Заполните название подключения, API key и API secret.');
        }

        $encrypted = encryptSensitive($apiSecret);
        $stmt = db()->prepare(
            'INSERT INTO user_bot_monitors (user_id, label, api_key, api_secret_encrypted, api_secret_iv)
             VALUES (:user_id, :label, :api_key, :api_secret_encrypted, :api_secret_iv)
             ON DUPLICATE KEY UPDATE
               label = VALUES(label),
               api_key = VALUES(api_key),
               api_secret_encrypted = VALUES(api_secret_encrypted),
               api_secret_iv = VALUES(api_secret_iv),
               last_sync_at = NULL,
               last_error = NULL,
               updated_at = CURRENT_TIMESTAMP'
        );
        $stmt->execute([
            'user_id' => $userId,
            'label' => $label,
            'api_key' => $apiKey,
            'api_secret_encrypted' => $encrypted['ciphertext'],
            'api_secret_iv' => $encrypted['iv'],
        ]);

        flash('success', 'API-ключ обновлён. Теперь мониторинг работает с новым подключением.');
        redirect('/bot_monitor.php');
    } catch (Throwable $e) {
        error_log('Bot monitor save error: ' . $e->getMessage());
        flash('error', 'Не удалось сохранить API-ключ: ' . $e->getMessage());
        redirect('/bot_monitor.php');
    }
}

$connection = null;
$snapshot = null;
$positions = [];
$chartData = [
    'available_bots' => [],
    'selected_bots' => [],
    'points' => [],
];

$today = new DateTimeImmutable('today');
$defaultStart = $today->modify('-29 days')->format('Y-m-d');
$defaultEnd = $today->format('Y-m-d');
$startDate = normalizeDateValue($_GET['start_date'] ?? null, $defaultStart);
$endDate = normalizeDateValue($_GET['end_date'] ?? null, $defaultEnd);

if ($startDate > $endDate) {
    [$startDate, $endDate] = [$endDate, $startDate];
}

$startObject = new DateTimeImmutable($startDate);
$endObject = new DateTimeImmutable($endDate);

if ($startObject < $endObject->modify('-180 days')) {
    $startObject = $endObject->modify('-180 days');
    $startDate = $startObject->format('Y-m-d');
}

$selectedBots = array_values(array_filter(array_map('strval', (array)($_GET['bots'] ?? []))));

try {
    $connection = loadMonitorConnection($userId);

    if ($connection) {
        $apiSecret = decryptSensitive((string)$connection['api_secret_encrypted'], (string)$connection['api_secret_iv']);
        $snapshot = fetchBotMonitorSnapshot((string)$connection['api_key'], $apiSecret);
        $positions = array_values(array_filter($snapshot['positions'] ?? [], static function (array $position): bool {
            return abs((float)($position['size'] ?? 0)) > 0;
        }));

        $closedPnl = fetchClosedPnlHistory(
            (string)$connection['api_key'],
            $apiSecret,
            $startObject->getTimestamp() * 1000,
            $endObject->modify('+1 day')->getTimestamp() * 1000 - 1
        );
        $chartData = buildTradeChartData($closedPnl, $selectedBots, $startDate, $endDate);

        saveMonitorStatus($userId, null);
    }
} catch (Throwable $e) {
    error_log('Bot monitor load error: ' . $e->getMessage());

    if ($connection) {
        saveMonitorStatus($userId, $e->getMessage());
    }

    $error = 'Не удалось получить данные из Cryptorg/Bybit API: ' . $e->getMessage();
}

$walletList = $snapshot['wallet']['list'] ?? [];
$account = is_array($walletList) && isset($walletList[0]) ? $walletList[0] : [];
$showApiForm = !$connection;
$summaryWalletBalance = (float)($account['totalWalletBalance'] ?? $account['totalEquity'] ?? 0);
$tradeSummary = buildTradeSummary($chartData['points'], $summaryWalletBalance);
$chartJson = json_encode($chartData['points'], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
?>
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Мониторинг ботов | ctgacademy.ru</title>
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>
<main class="container dashboard">
  <?php if ($success): ?>
    <div class="alert success"><?= h($success) ?></div>
  <?php endif; ?>

  <?php if ($error): ?>
    <div class="alert error"><?= h($error) ?></div>
  <?php endif; ?>

  <article class="card">
    <div class="admin-header">
      <div>
        <h1>Мониторинг ботов</h1>
        <?php if ($connection): ?>
          <p class="muted">Подключение: <?= h($connection['label']) ?>. Последняя синхронизация: <?= h($connection['last_sync_at'] ?? 'ещё не было') ?>.</p>
        <?php else: ?>
          <p class="muted">Добавьте API-ключ Cryptorg Bybit Liquidity, чтобы увидеть баланс, позиции и историю сделок.</p>
        <?php endif; ?>
      </div>
      <div class="admin-actions">
        <a href="/dashboard.php">Назад в кабинет</a>
        <?php if ($connection): ?>
          <button type="button" data-api-toggle>Сменить API ключ</button>
          <a href="/bot_monitor.php?start_date=<?= h($startDate) ?>&end_date=<?= h($endDate) ?>">Обновить данные</a>
        <?php endif; ?>
      </div>
    </div>

    <section class="card api-form-card" <?= $showApiForm ? '' : 'hidden' ?> data-api-form>
      <h2><?= $connection ? 'Смена API ключа' : 'Подключение API' ?></h2>
      <form class="form admin-form" method="post" action="/bot_monitor.php">
        <label>
          Название подключения
          <input type="text" name="label" maxlength="120" required value="<?= h($connection['label'] ?? 'Мой бот') ?>" />
        </label>

        <label>
          API key
          <input type="text" name="api_key" autocomplete="off" required value="" />
        </label>

        <label>
          API secret
          <input type="password" name="api_secret" autocomplete="new-password" required value="" />
        </label>

        <p class="muted">После сохранения старый ключ будет заменён новым. Секрет хранится в базе в зашифрованном виде.</p>

        <div class="admin-actions">
          <button type="submit">Сохранить API ключ</button>
          <?php if ($connection): ?>
            <button type="button" data-api-toggle>Отмена</button>
          <?php endif; ?>
        </div>
      </form>
    </section>

    <?php if ($connection && $snapshot): ?>
      <section class="course-section" aria-label="Сводка по счёту">
        <div class="monitor-kpis">
          <div class="lesson-card">
            <strong>Эквити</strong>
            <p><?= h(formatNumber($account['totalEquity'] ?? 0, 2)) ?> USDT</p>
          </div>
          <div class="lesson-card">
            <strong>Баланс кошелька</strong>
            <p><?= h(formatNumber($account['totalWalletBalance'] ?? 0, 2)) ?> USDT</p>
          </div>
          <div class="lesson-card">
            <strong>Доступный баланс</strong>
            <p><?= h(formatNumber($account['totalAvailableBalance'] ?? 0, 2)) ?> USDT</p>
          </div>
        </div>
      </section>

      <section class="course-section" aria-label="Открытые позиции">
        <h2>Открытые позиции</h2>
        <div class="table-wrap">
          <table class="monitor-table">
            <thead>
              <tr>
                <th>Символ</th>
                <th>Сторона</th>
                <th>Размер</th>
                <th>Вход</th>
                <th>Mark</th>
                <th>PNL</th>
              </tr>
            </thead>
            <tbody>
              <?php if (!$positions): ?>
                <tr><td colspan="6">Сейчас открытых позиций нет.</td></tr>
              <?php endif; ?>
              <?php foreach ($positions as $position): ?>
                <tr>
                  <td><?= h($position['symbol'] ?? '') ?></td>
                  <td><?= h($position['side'] ?? '') ?></td>
                  <td><?= h(formatNumber($position['size'] ?? 0, 4)) ?></td>
                  <td><?= h(formatNumber($position['avgPrice'] ?? 0, 4)) ?></td>
                  <td><?= h(formatNumber($position['markPrice'] ?? 0, 4)) ?></td>
                  <td><?= h(formatNumber($position['unrealisedPnl'] ?? 0, 4)) ?></td>
                </tr>
              <?php endforeach; ?>
            </tbody>
          </table>
        </div>
      </section>

      <section class="course-section" aria-label="История сделок">
        <div class="monitor-section-head">
          <div>
            <h2>История сделок</h2>
            <p class="muted">Линия показывает кумулятивную прибыль, столбики снизу показывают количество закрытых сделок за день.</p>
          </div>
        </div>

        <form class="monitor-filter-form" method="get" action="/bot_monitor.php">
          <label>
            С даты
            <input type="date" name="start_date" value="<?= h($startDate) ?>" />
          </label>

          <label>
            По дату
            <input type="date" name="end_date" value="<?= h($endDate) ?>" />
          </label>

          <fieldset class="bot-filter">
            <legend>Торговая пара</legend>
            <label class="consent-check">
              <input type="checkbox" name="all_bots" value="1" <?= $chartData['selected_bots'] ? '' : 'checked' ?> data-all-bots />
              <span class="consent-text">Все пары</span>
            </label>

            <div class="bot-options">
              <?php if (!$chartData['available_bots']): ?>
                <p class="muted">За выбранный период сделок не найдено.</p>
              <?php endif; ?>
              <?php foreach ($chartData['available_bots'] as $bot): ?>
                <label class="consent-check">
                  <input type="checkbox" name="bots[]" value="<?= h($bot) ?>" <?= in_array($bot, $chartData['selected_bots'], true) ? 'checked' : '' ?> data-bot-checkbox />
                  <span class="consent-text"><?= h($bot) ?></span>
                </label>
              <?php endforeach; ?>
            </div>
          </fieldset>

          <div class="admin-actions">
            <button type="submit">Показать</button>
          </div>
        </form>

        <div class="trade-chart-wrap">
          <canvas id="tradeHistoryChart" width="960" height="420" aria-label="График истории сделок"></canvas>
          <div class="chart-tooltip" data-chart-tooltip hidden></div>
        </div>

        <div class="monitor-summary-grid">
          <div class="lesson-card">
            <strong>Кумулятивная прибыль за выбранный период</strong>
            <p><?= h(formatNumber($tradeSummary['total_profit'], 2)) ?> USDT</p>
          </div>
          <div class="lesson-card">
            <strong>Количество сделок за выбранный период</strong>
            <p><?= h((string)$tradeSummary['total_trades']) ?></p>
          </div>
          <div class="lesson-card">
            <strong>Доходность в % за выбранный период</strong>
            <p><?= h(formatNumber($tradeSummary['period_return'], 2)) ?>%</p>
          </div>
          <div class="lesson-card">
            <strong>Средняя прибыль в день</strong>
            <p><?= h(formatNumber($tradeSummary['avg_profit_day'], 2)) ?> USDT</p>
          </div>
          <div class="lesson-card">
            <strong>Среднее количество сделок в день</strong>
            <p><?= h(formatNumber($tradeSummary['avg_trades_day'], 2)) ?></p>
          </div>
          <div class="lesson-card">
            <strong>Среднегодовая доходность</strong>
            <p><?= h(formatNumber($tradeSummary['annual_return'], 2)) ?>%</p>
          </div>
        </div>
      </section>
    <?php endif; ?>
  </article>
</main>

<?php require __DIR__ . '/footer.php'; ?>

<script>
  const apiToggles = document.querySelectorAll('[data-api-toggle]');
  const apiForm = document.querySelector('[data-api-form]');

  apiToggles.forEach((button) => {
    button.addEventListener('click', () => {
      if (apiForm) {
        apiForm.hidden = !apiForm.hidden;
      }
    });
  });

  const allBots = document.querySelector('[data-all-bots]');
  const botCheckboxes = document.querySelectorAll('[data-bot-checkbox]');

  if (allBots) {
    allBots.addEventListener('change', () => {
      if (allBots.checked) {
        botCheckboxes.forEach((checkbox) => {
          checkbox.checked = false;
        });
      }
    });
  }

  botCheckboxes.forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      if (checkbox.checked && allBots) {
        allBots.checked = false;
      }
    });
  });

  const chartData = <?= $chartJson ?>;
  const canvas = document.getElementById('tradeHistoryChart');
  const tooltip = document.querySelector('[data-chart-tooltip]');
  const chartState = {
    points: [],
    activeIndex: null
  };

  function formatChartNumber(value, decimals = 2) {
    return Number(value).toLocaleString('ru-RU', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });
  }

  function drawTradeChart() {
    if (!canvas) {
      return;
    }

    const context = canvas.getContext('2d');
    const scale = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || canvas.width;
    const height = canvas.clientHeight || canvas.height;

    canvas.width = width * scale;
    canvas.height = height * scale;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, width, height);
    chartState.points = [];

    const padding = { top: 28, right: 28, bottom: 58, left: 72 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;

    context.fillStyle = 'rgba(6, 12, 26, 0.35)';
    context.fillRect(0, 0, width, height);

    if (!chartData.length) {
      context.fillStyle = '#c5cce3';
      context.font = '16px Inter, Segoe UI, Arial, sans-serif';
      context.fillText('Нет данных для графика за выбранный период.', padding.left, height / 2);
      return;
    }

    const pnlValues = chartData.map((point) => Number(point.pnl));
    const tradeValues = chartData.map((point) => Number(point.trades));
    const minPnl = Math.min(0, ...pnlValues);
    const maxPnl = Math.max(0, ...pnlValues);
    const pnlRange = maxPnl - minPnl || 1;
    const maxTrades = Math.max(1, ...tradeValues);
    const barAreaHeight = plotHeight * 0.28;
    const lineAreaHeight = plotHeight - barAreaHeight - 18;
    const zeroY = padding.top + lineAreaHeight - ((0 - minPnl) / pnlRange) * lineAreaHeight;
    const stepX = chartData.length > 1 ? plotWidth / (chartData.length - 1) : plotWidth;

    context.strokeStyle = 'rgba(153, 182, 255, 0.18)';
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(padding.left, padding.top);
    context.lineTo(padding.left, padding.top + plotHeight);
    context.lineTo(width - padding.right, padding.top + plotHeight);
    context.stroke();

    context.strokeStyle = 'rgba(255, 255, 255, 0.18)';
    context.beginPath();
    context.moveTo(padding.left, zeroY);
    context.lineTo(width - padding.right, zeroY);
    context.stroke();

    const barWidth = Math.max(3, Math.min(22, plotWidth / chartData.length * 0.58));
    chartData.forEach((point, index) => {
      const x = padding.left + (chartData.length > 1 ? index * stepX : plotWidth / 2);
      const barHeight = (Number(point.trades) / maxTrades) * barAreaHeight;
      const barX = x - barWidth / 2;
      const barY = padding.top + lineAreaHeight + 18 + (barAreaHeight - barHeight);

      context.fillStyle = 'rgba(87, 181, 255, 0.38)';
      context.fillRect(barX, barY, barWidth, barHeight);
    });

    context.strokeStyle = '#3ddc97';
    context.lineWidth = 3;
    context.beginPath();
    chartData.forEach((point, index) => {
      const x = padding.left + (chartData.length > 1 ? index * stepX : plotWidth / 2);
      const y = padding.top + lineAreaHeight - ((Number(point.pnl) - minPnl) / pnlRange) * lineAreaHeight;
      chartState.points.push({ x, y, point, index });

      if (index === 0) {
        context.moveTo(x, y);
      } else {
        context.lineTo(x, y);
      }
    });
    context.stroke();

    context.fillStyle = '#f2f5ff';
    chartState.points.forEach(({ x, y, index }) => {
      context.beginPath();
      context.arc(x, y, chartState.activeIndex === index ? 6 : 3.5, 0, Math.PI * 2);
      context.fill();
    });

    if (chartState.activeIndex !== null && chartState.points[chartState.activeIndex]) {
      const active = chartState.points[chartState.activeIndex];
      context.strokeStyle = 'rgba(242, 245, 255, 0.35)';
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(active.x, padding.top);
      context.lineTo(active.x, padding.top + plotHeight);
      context.stroke();

      context.fillStyle = '#57b5ff';
      context.beginPath();
      context.arc(active.x, active.y, 7, 0, Math.PI * 2);
      context.fill();
    }

    context.fillStyle = '#c5cce3';
    context.font = '12px Inter, Segoe UI, Arial, sans-serif';
    context.fillText(`${maxPnl.toFixed(2)} USDT`, 12, padding.top + 8);
    context.fillText(`${minPnl.toFixed(2)} USDT`, 12, padding.top + lineAreaHeight);
    context.fillText(`Сделки: ${maxTrades}`, 12, padding.top + lineAreaHeight + 28);

    const labelEvery = Math.max(1, Math.ceil(chartData.length / 8));
    chartData.forEach((point, index) => {
      if (index % labelEvery !== 0 && index !== chartData.length - 1) {
        return;
      }

      const x = padding.left + (chartData.length > 1 ? index * stepX : plotWidth / 2);
      context.save();
      context.translate(x, height - 26);
      context.rotate(-Math.PI / 7);
      context.textAlign = 'right';
      context.fillText(point.label, 0, 0);
      context.restore();
    });
  }

  function nearestChartPoint(event) {
    if (!canvas || !chartState.points.length) {
      return null;
    }

    const rect = canvas.getBoundingClientRect();
    const clientX = event.touches ? event.touches[0].clientX : event.clientX;
    const clientY = event.touches ? event.touches[0].clientY : event.clientY;
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let nearest = null;
    let nearestDistance = Infinity;

    chartState.points.forEach((item) => {
      const distance = Math.hypot(item.x - x, item.y - y);

      if (distance < nearestDistance) {
        nearest = item;
        nearestDistance = distance;
      }
    });

    return nearestDistance <= 36 ? nearest : null;
  }

  function showTooltip(item) {
    if (!tooltip || !item) {
      return;
    }

    chartState.activeIndex = item.index;
    drawTradeChart();

    const point = item.point;
    tooltip.innerHTML = `
      <strong>${point.display_date}</strong>
      <span>PNL за день: ${formatChartNumber(point.day_pnl)} USDT</span>
      <span>PNL кумулятивный: ${formatChartNumber(point.pnl)} USDT</span>
      <span>Сделок: ${Number(point.trades)}</span>
    `;
    tooltip.hidden = false;

    const canvasRect = canvas.getBoundingClientRect();
    const wrapRect = canvas.parentElement.getBoundingClientRect();
    const left = canvasRect.left - wrapRect.left + item.x;
    const top = canvasRect.top - wrapRect.top + item.y;
    const maxLeft = wrapRect.width - tooltip.offsetWidth - 12;

    tooltip.style.left = `${Math.max(12, Math.min(maxLeft, left + 12))}px`;
    tooltip.style.top = `${Math.max(12, top - tooltip.offsetHeight - 12)}px`;
  }

  function hideTooltip() {
    if (tooltip) {
      tooltip.hidden = true;
    }

    chartState.activeIndex = null;
    drawTradeChart();
  }

  if (canvas) {
    canvas.addEventListener('mousemove', (event) => {
      const nearest = nearestChartPoint(event);

      if (nearest) {
        showTooltip(nearest);
      } else if (tooltip && !tooltip.hidden) {
        hideTooltip();
      }
    });

    canvas.addEventListener('mouseleave', hideTooltip);
    canvas.addEventListener('click', (event) => {
      const nearest = nearestChartPoint(event);

      if (nearest) {
        showTooltip(nearest);
      }
    });
    canvas.addEventListener('touchstart', (event) => {
      const nearest = nearestChartPoint(event);

      if (nearest) {
        event.preventDefault();
        showTooltip(nearest);
      }
    }, { passive: false });
  }

  drawTradeChart();
  window.addEventListener('resize', drawTradeChart);
</script>
</body>
</html>
