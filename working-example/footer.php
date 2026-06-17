<?php

declare(strict_types=1);

$currentYear = (int)date('Y');
$footerYears = $currentYear > 2026 ? '2026-' . $currentYear : '2026';
?>
<footer class="site-footer">
  <div class="container site-footer-inner">
    <div><?= htmlspecialchars($footerYears) ?> &copy; ctgacademy.ru</div>
    <a href="mailto:support@ctgacademy.ru">support@ctgacademy.ru</a>
  </div>
</footer>
