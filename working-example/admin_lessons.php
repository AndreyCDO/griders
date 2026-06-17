<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

requireAdmin();

$editingLessonId = isset($_GET['edit']) ? (int)$_GET['edit'] : null;
$editingLesson = null;
$formValues = [
    'position' => '',
    'title' => '',
    'short_description' => '',
    'video_youtube' => '',
    'video_rutube' => '',
    'content' => '',
];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $lessonId = (int)($_POST['lesson_id'] ?? 0);
    $formValues = [
        'position' => trim((string)($_POST['position'] ?? '')),
        'title' => trim((string)($_POST['title'] ?? '')),
        'short_description' => trim((string)($_POST['short_description'] ?? '')),
        'video_youtube' => trim((string)($_POST['video_youtube'] ?? '')),
        'video_rutube' => trim((string)($_POST['video_rutube'] ?? '')),
        'content' => trim((string)($_POST['content'] ?? '')),
    ];

    $errors = [];
    $position = filter_var($formValues['position'], FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);

    if ($position === false) {
        $errors[] = 'Позиция урока должна быть числом больше нуля.';
    }

    if ($formValues['title'] === '') {
        $errors[] = 'Укажите название урока.';
    }

    if ($formValues['short_description'] === '') {
        $errors[] = 'Добавьте короткое описание.';
    }

    if ($formValues['content'] === '') {
        $errors[] = 'Заполните содержимое урока.';
    }

    foreach (['video_youtube' => 'YouTube', 'video_rutube' => 'RuTube'] as $field => $label) {
        if ($formValues[$field] !== '' && filter_var($formValues[$field], FILTER_VALIDATE_URL) === false) {
            $errors[] = "Ссылка {$label} должна быть корректным URL.";
        }
    }

    if (!$errors) {
        try {
            if ($lessonId > 0) {
                $stmt = db()->prepare(
                    'UPDATE lessons
                     SET position = :position,
                         title = :title,
                         short_description = :short_description,
                         video_youtube = :video_youtube,
                         video_rutube = :video_rutube,
                         content = :content,
                         updated_at = CURRENT_TIMESTAMP
                     WHERE id = :id'
                );
                $stmt->execute([
                    'id' => $lessonId,
                    'position' => $position,
                    'title' => $formValues['title'],
                    'short_description' => $formValues['short_description'],
                    'video_youtube' => $formValues['video_youtube'] !== '' ? $formValues['video_youtube'] : null,
                    'video_rutube' => $formValues['video_rutube'] !== '' ? $formValues['video_rutube'] : null,
                    'content' => $formValues['content'],
                ]);

                flash('success', 'Урок обновлён.');
                redirect('/admin_lessons.php?edit=' . $lessonId);
            }

            $stmt = db()->prepare(
                'INSERT INTO lessons (position, title, short_description, video_youtube, video_rutube, content)
                 VALUES (:position, :title, :short_description, :video_youtube, :video_rutube, :content)'
            );
            $stmt->execute([
                'position' => $position,
                'title' => $formValues['title'],
                'short_description' => $formValues['short_description'],
                'video_youtube' => $formValues['video_youtube'] !== '' ? $formValues['video_youtube'] : null,
                'video_rutube' => $formValues['video_rutube'] !== '' ? $formValues['video_rutube'] : null,
                'content' => $formValues['content'],
            ]);

            flash('success', 'Урок создан.');
            redirect('/admin_lessons.php?edit=' . (int)db()->lastInsertId());
        } catch (PDOException $e) {
            if ($e->getCode() === '23000') {
                $errors[] = 'Урок с такой позицией уже существует. Выберите другую позицию.';
            } else {
                error_log('Admin lesson save error: ' . $e->getMessage());
                $errors[] = 'Не удалось сохранить урок.';
            }
        } catch (Throwable $e) {
            error_log('Admin lesson save error: ' . $e->getMessage());
            $errors[] = 'Не удалось сохранить урок.';
        }
    }

    if ($errors) {
        flash('error', implode(' ', $errors));
        $redirect = '/admin_lessons.php';
        if ($lessonId > 0) {
            $redirect .= '?edit=' . $lessonId;
        }
        $_SESSION['lesson_form'] = $formValues;
        redirect($redirect);
    }
}

try {
    $lessonStmt = db()->query(
        'SELECT id, position, title, short_description, video_youtube, video_rutube, content, updated_at
         FROM lessons
         ORDER BY position ASC, id ASC'
    );
    $lessons = $lessonStmt->fetchAll();

    if ($editingLessonId !== null) {
        foreach ($lessons as $lesson) {
            if ((int)$lesson['id'] === $editingLessonId) {
                $editingLesson = $lesson;
                break;
            }
        }
    }
} catch (Throwable $e) {
    error_log('Admin lesson load error: ' . $e->getMessage());
    flash('error', 'Не удалось загрузить уроки. Проверьте структуру базы данных.');
    redirect('/dashboard.php');
}

if (isset($_SESSION['lesson_form']) && is_array($_SESSION['lesson_form'])) {
    $formValues = array_merge($formValues, $_SESSION['lesson_form']);
    unset($_SESSION['lesson_form']);
} elseif ($editingLesson) {
    $formValues = [
        'position' => (string)$editingLesson['position'],
        'title' => (string)$editingLesson['title'],
        'short_description' => (string)$editingLesson['short_description'],
        'video_youtube' => (string)($editingLesson['video_youtube'] ?? ''),
        'video_rutube' => (string)($editingLesson['video_rutube'] ?? ''),
        'content' => (string)$editingLesson['content'],
    ];
}

$success = flash('success');
$error = flash('error');
$isEditing = $editingLesson !== null;
?>
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Админка уроков | Академия</title>
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

  <section class="admin-header">
    <div>
      <h1>Управление уроками</h1>
      <p class="muted">Создание и редактирование уроков прямо в базе данных.</p>
    </div>

    <div class="admin-actions">
      <a href="/dashboard.php">К кабинету</a>
      <a href="/admin_lessons.php">Новый урок</a>
    </div>
  </section>

  <section class="admin-layout">
    <article class="card">
      <h2>Список уроков</h2>

      <div class="admin-lesson-list">
        <?php if (!$lessons): ?>
          <p class="muted">Уроков пока нет.</p>
        <?php endif; ?>

        <?php foreach ($lessons as $lesson): ?>
          <?php $lessonId = (int)$lesson['id']; ?>
          <a
            class="admin-lesson-item<?= $editingLessonId === $lessonId ? ' is-active' : '' ?>"
            href="/admin_lessons.php?edit=<?= $lessonId ?>"
          >
            <span class="admin-lesson-position">#<?= (int)$lesson['position'] ?></span>
            <strong><?= htmlspecialchars((string)$lesson['title']) ?></strong>
            <span class="muted"><?= htmlspecialchars((string)$lesson['short_description']) ?></span>
          </a>
        <?php endforeach; ?>
      </div>
    </article>

    <article class="card">
      <h2><?= $isEditing ? 'Редактирование урока' : 'Новый урок' ?></h2>

      <form method="post" class="form admin-form">
        <input type="hidden" name="lesson_id" value="<?= $isEditing ? (int)$editingLesson['id'] : 0 ?>" />

        <label>
          Позиция
          <input type="number" name="position" min="1" required value="<?= htmlspecialchars($formValues['position']) ?>" />
        </label>

        <label>
          Название
          <input type="text" name="title" maxlength="255" required value="<?= htmlspecialchars($formValues['title']) ?>" />
        </label>

        <label>
          Короткое описание
          <textarea name="short_description" rows="3" maxlength="500" required><?= htmlspecialchars($formValues['short_description']) ?></textarea>
        </label>

        <label>
          Видео YouTube
          <input type="url" name="video_youtube" placeholder="https://www.youtube.com/embed/..." value="<?= htmlspecialchars($formValues['video_youtube']) ?>" />
        </label>

        <label>
          Видео RuTube
          <input type="url" name="video_rutube" placeholder="https://rutube.ru/play/embed/..." value="<?= htmlspecialchars($formValues['video_rutube']) ?>" />
        </label>

        <label>
          Содержимое урока
          <textarea name="content" rows="18" required><?= htmlspecialchars($formValues['content']) ?></textarea>
        </label>

        <div class="admin-actions">
          <button type="submit"><?= $isEditing ? 'Сохранить изменения' : 'Создать урок' ?></button>
          <?php if ($isEditing): ?>
            <a href="/lesson.php?id=<?= (int)$editingLesson['id'] ?>">Открыть урок</a>
          <?php endif; ?>
        </div>
      </form>
    </article>
  </section>
</main>
<?php require __DIR__ . '/footer.php'; ?>
</body>
</html>
