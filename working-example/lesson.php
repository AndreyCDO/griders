<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

requireAuth();

$user = currentUser();
$userId = (int)$user['id'];
$lessonId = (int)($_GET['id'] ?? $_POST['lesson_id'] ?? 0);

if ($lessonId <= 0) {
    flash('error', 'Урок не найден.');
    redirect('/dashboard.php');
}

try {
    $lessonStmt = db()->query('SELECT * FROM lessons ORDER BY position ASC, id ASC');
    $lessons = $lessonStmt->fetchAll();

    if (!$lessons) {
        flash('error', 'Уроки пока не добавлены.');
        redirect('/dashboard.php');
    }

    $lesson = null;
    $lessonIndex = null;

    foreach ($lessons as $index => $item) {
        if ((int)$item['id'] === $lessonId) {
            $lesson = $item;
            $lessonIndex = $index;
            break;
        }
    }

    if ($lesson === null || $lessonIndex === null) {
        flash('error', 'Урок не найден.');
        redirect('/dashboard.php');
    }

    $doneStmt = db()->prepare('SELECT lesson_id FROM lesson_progress WHERE user_id = :user_id');
    $doneStmt->execute(['user_id' => $userId]);
    $done = array_map(static fn(array $row): int => (int)$row['lesson_id'], $doneStmt->fetchAll());
    $doneSet = array_flip($done);

    $previousLessonId = $lessonIndex > 0 ? (int)$lessons[$lessonIndex - 1]['id'] : null;
    $nextLessonId = isset($lessons[$lessonIndex + 1]) ? (int)$lessons[$lessonIndex + 1]['id'] : null;

    $isUnlocked = $lessonIndex === 0 || ($previousLessonId !== null && isset($doneSet[$previousLessonId]));
    $isCompleted = isset($doneSet[$lessonId]);

    if (!$isUnlocked) {
        flash('error', 'Сначала завершите предыдущий урок.');
        redirect('/dashboard.php');
    }

    if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['lesson_id'])) {
        $markComplete = db()->prepare(
            'INSERT INTO lesson_progress (user_id, lesson_id, completed_at)
             VALUES (:user_id, :lesson_id, NOW())
             ON DUPLICATE KEY UPDATE completed_at = VALUES(completed_at)'
        );
        $markComplete->execute([
            'user_id' => $userId,
            'lesson_id' => $lessonId,
        ]);

        flash('success', 'Урок отмечен как пройденный.');
        redirect('/lesson.php?id=' . $lessonId);
    }

} catch (Throwable $e) {
    error_log('Lesson page error: ' . $e->getMessage());
    flash('error', 'Ошибка при загрузке урока.');
    redirect('/dashboard.php');
}
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
  <title><?= htmlspecialchars((string)$lesson['title']) ?> | Академия</title>
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>

<main class="container dashboard">
  <article class="card">

    <a class="lesson-back" href="/dashboard.php">← Назад к урокам</a>

    <h1><?= htmlspecialchars((string)$lesson['title']) ?></h1>
    <p class="muted"><?= htmlspecialchars((string)$lesson['short_description']) ?></p>

    <?php if (!empty($lesson['video_youtube']) || !empty($lesson['video_rutube'])): ?>
      <?php $lessonVideoUrl = preferredVideoUrl($lesson['video_youtube'] ?? null, $lesson['video_rutube'] ?? null); ?>
      <div class="video-wrapper">
        <iframe
          src="<?= htmlspecialchars($lessonVideoUrl) ?>"
          title="<?= htmlspecialchars((string)$lesson['title']) ?>"
          frameborder="0"
          allow="clipboard-write; autoplay"
          webkitAllowFullScreen
          mozallowfullscreen
          allowfullscreen
        ></iframe>
      </div>
    <?php endif; ?>

    <!-- 📄 КОНТЕНТ -->
    <div class="lesson-content">
  <?php
    $content = htmlspecialchars((string)$lesson['content']);

    // превращаем ссылки в кликабельные
    $content = preg_replace(
        '~(https?://[^\s]+)~',
        '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>',
        $content
    );

    echo nl2br($content);
  ?>
</div>

    <!-- ✅ КНОПКА ПРОЙТИ -->
    <?php if (!$isCompleted): ?>
      <form method="post" class="lesson-form">
        <input type="hidden" name="lesson_id" value="<?= $lessonId ?>" />
        <button type="submit">☑ Завершить урок</button>
      </form>
    <?php else: ?>
      <p class="muted">Урок пройден ✅</p>
    <?php endif; ?>

    <!-- 👉 СЛЕДУЮЩИЙ УРОК -->
    <?php if ($isCompleted && $nextLessonId): ?>
      <a class="lesson-link" href="/lesson.php?id=<?= $nextLessonId ?>">
        → Перейти к следующему уроку
      </a>
    <?php endif; ?>

  </article>
</main>

<?php require __DIR__ . '/footer.php'; ?>

</body>
</html>
