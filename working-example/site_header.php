<?php

declare(strict_types=1);

$user = currentUser();
$navItems = ['Главная' => '/index.php', 'GRID Radar' => '/grid_radar.php'];

if ($user) {
    $navItems['Обучение'] = '/dashboard.php';
    $navItems['Мониторинг'] = '/bot_monitor.php';

    if (isAdmin()) {
        $navItems['Демотрейдинг'] = '/demo_trading.php';
        $navItems['Админка'] = '/admin_lessons.php';
    }

    $navItems['Выход'] = '/logout.php';
} else {
    $navItems['Вход'] = '/index.php?auth=login';
    $navItems['Регистрация'] = '/index.php?auth=register';
}

$currentPath = parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/';

if ($currentPath === '/') {
    $currentPath = '/index.php';
}
?>

<header class="site-header">
  <div class="container site-header-inner">
    <a class="site-logo" href="/index.php">Крипторг Академия</a>
    <nav class="site-nav" aria-label="Основная навигация">
      <?php foreach ($navItems as $label => $url): ?>
        <a class="<?= $currentPath === $url ? 'is-active' : '' ?>" href="<?= htmlspecialchars($url) ?>">
          <?= htmlspecialchars($label) ?>
        </a>
      <?php endforeach; ?>
    </nav>
  </div>
</header>
