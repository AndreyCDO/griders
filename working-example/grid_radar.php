<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

$gridRadarHtml = contentHtml(
    'grid-radar-content',
    '<h1>GRID Radar — готовые настройки торговых ботов Cryptorg</h1><p>GRID Radar — это закрытый канал с актуальными настройками торговых ботов для платформы Cryptorg.</p>'
);

$rutubeUrl = 'https://rutube.ru/play/embed/efdbfb8ad0005b28f1bd9f4700b3cfa8';
$youtubeUrl = 'https://www.youtube.com/embed/FJzztOP-PQQ';
$videoUrl = preferredVideoUrl($youtubeUrl, $rutubeUrl);
?>
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GRID Radar | Крипторг Академия</title>
  <meta name="description" content="GRID Radar — готовые настройки торговых ботов Cryptorg." />
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>

<main class="container dashboard">
  <article class="card">
    <section aria-label="Видео GRID Radar">
      <h1>GRID Radar</h1>

      <div class="video-wrapper">
        <iframe
          id="gridRadarVideo"
          src="<?= htmlspecialchars($videoUrl) ?>"
          title="GRID Radar"
          frameborder="0"
          allow="clipboard-write; autoplay"
          webkitAllowFullScreen
          mozallowfullscreen
          allowfullscreen
        ></iframe>
      </div>
    </section>

    <section class="course-section content-card" aria-label="Описание GRID Radar">
      <?= $gridRadarHtml ?>
    </section>
  </article>
</main>

<?php require __DIR__ . '/footer.php'; ?>
</body>
</html>
