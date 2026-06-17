<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

requireAuth();

$user = currentUser();
$userId = (int)$user['id'];
$isAdminUser = isAdmin();
$introRutubeUrl = 'https://rutube.ru/play/embed/706590770f4df97fdad7166de38cbce5';
$introYoutubeUrl = 'https://www.youtube.com/embed/EkWvtA959rg';
$introVideoUrl = preferredVideoUrl($introYoutubeUrl, $introRutubeUrl);

try {
    $lessonStmt = db()->query('SELECT id, position, title, short_description FROM lessons ORDER BY position ASC, id ASC');
    $lessons = $lessonStmt->fetchAll();

    $doneStmt = db()->prepare('SELECT lesson_id FROM lesson_progress WHERE user_id = :user_id');
    $doneStmt->execute(['user_id' => $userId]);
    $done = array_map(static fn(array $row): int => (int)$row['lesson_id'], $doneStmt->fetchAll());
    $doneSet = array_flip($done);

} catch (Throwable $e) {
    error_log('Dashboard load error: ' . $e->getMessage());
    flash('error', 'Ошибка при загрузке уроков. Проверьте базу данных.');
    redirect('/dashboard.php');
}

$success = flash('success');
$error = flash('error');
?>

<!doctype html>
<html lang="ru">
<head>
  <!-- Yandex.Metrika counter -->
<script type="text/javascript">
    (function(m,e,t,r,i,k,a){
        m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
        m[i].l=1*new Date();
        for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
        k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)
    })(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id=108565187', 'ym');

    ym(108565187, 'init', {ssr:true, webvisor:true, clickmap:true, ecommerce:"dataLayer", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true});
</script>
<noscript><div><img src="https://mc.yandex.ru/watch/108565187" style="position:absolute; left:-9999px;" alt="" /></div></noscript>
<!-- /Yandex.Metrika counter -->
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Личный кабинет | Академия Криптотрейдинга</title>
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>

<body>
<?php require __DIR__ . '/site_header.php'; ?>
<main class="container dashboard">

  <?php if ($success): ?>
    <div class="alert success"><?= htmlspecialchars($success) ?></div>
  <?php endif; ?>

  <?php if ($error): ?>
    <div class="alert error"><?= htmlspecialchars($error) ?></div>
  <?php endif; ?>
<!--
  <section class="useful-links" aria-label="Полезные ссылки">
    <div class="card">
      <h2>Полезные ссылки</h2>
      <div class="link-grid">
        <a href="https://cryptorg.net/?ref=101002" target="_blank" rel="noopener noreferrer">Торгуем на Cryptorg</a>
        <a href="https://t.me/tribute/app?startapp=sSyb" target="_blank" rel="noopener noreferrer">Настройки ботов GRID Radar</a>
      </div>
    </div>
  </section>
  -->

  <article class="card">

    <h1>Добро пожаловать в крипторг академию</h1>
    <p class="muted">Желаем успешного обучения и прибыльной торговли!</p>

    <!-- 🎬 VIDEO BLOCK -->
    <div class="card" style="margin-top: 1.5rem;">

      <h2>Вводное занятие</h2>

      <div class="video-wrapper">
        <iframe
          id="mainVideo"
          src="<?= htmlspecialchars($introVideoUrl) ?>"
          title="Приветственное видео"
          frameborder="0"
          allow="clipboard-write; autoplay"
          webkitAllowFullScreen
          mozallowfullscreen
          allowfullscreen
        ></iframe>
      </div>

    </div>

    <!-- 📚 LESSONS -->
    <section class="course-section" aria-label="Курс для начинающих">

      <h2>Курс для начинающих</h2>
      <p class="muted">
        Нажмите на урок, чтобы открыть материал. Уроки открываются по порядку.
      </p>

      <div class="lesson-list">

        <?php if (!$lessons): ?>
          <article class="lesson-card locked">
            <p class="muted">Уроки пока не добавлены.</p>
          </article>
        <?php endif; ?>

        <?php foreach ($lessons as $index => $lesson): ?>
          <?php
            $lessonId = (int)$lesson['id'];
            $isCompleted = isset($doneSet[$lessonId]);

            $prevLessonId = $index > 0 ? (int)$lessons[$index - 1]['id'] : null;
            $isUnlocked = $index === 0 || ($prevLessonId !== null && isset($doneSet[$prevLessonId]));

            $lessonClass = $isCompleted
                ? 'lesson-card done'
                : ($isUnlocked ? 'lesson-card unlocked' : 'lesson-card locked');
          ?>

          <article class="<?= $lessonClass ?>">

            <header class="lesson-head">
              <h3><?= htmlspecialchars((string)$lesson['title']) ?></h3>

              <?php if ($isCompleted): ?>
                <span class="badge done">Пройден</span>
              <?php elseif ($isUnlocked): ?>
                <span class="badge open">Доступен</span>
              <?php else: ?>
                <span class="badge lock">Закрыт</span>
              <?php endif; ?>
            </header>

            <?php if ($isUnlocked || $isCompleted): ?>
              <a class="lesson-link" href="/lesson.php?id=<?= $lessonId ?>">
                <?= htmlspecialchars((string)$lesson['short_description']) ?>
              </a>
            <?php else: ?>
              <p class="muted">Урок станет доступен после прохождения предыдущего.</p>
            <?php endif; ?>

            <?php if ($isCompleted): ?>
              <p class="muted">Урок пройден — можно повторить в любое время.</p>
            <?php endif; ?>

          </article>
        <?php endforeach; ?>

      </div>
    </section>

    <div class="dashboard-actions">
      <a href="/bot_monitor.php">Мониторинг ботов</a>
      <a class="logout" href="/logout.php">Выйти</a>
      <?php if ($isAdminUser): ?>
        <a class="admin-link" href="/admin_lessons.php">Админка уроков</a>
      <?php endif; ?>
    </div>

  </article>

</main>

<?php require __DIR__ . '/footer.php'; ?>

</body>
</html>
