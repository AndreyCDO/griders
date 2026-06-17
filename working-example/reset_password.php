<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

if (currentUser()) {
    redirect('/dashboard.php');
}

$token = trim((string)($_GET['token'] ?? $_POST['token'] ?? ''));

if ($token === '') {
    flash('error', 'Некорректная ссылка для восстановления пароля.');
    redirect('/forgot_password.php');
}

$success = null;
$error = null;

try {
    $stmt = db()->prepare(
        'SELECT id
         FROM users
         WHERE password_reset_token = :token
           AND password_reset_expires_at IS NOT NULL
           AND password_reset_expires_at >= NOW()
         LIMIT 1'
    );
    $stmt->execute(['token' => $token]);
    $user = $stmt->fetch();

    if (!$user) {
        flash('error', 'Ссылка для восстановления недействительна или срок её действия истёк.');
        redirect('/forgot_password.php');
    }

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $password = (string)($_POST['password'] ?? '');
        $passwordConfirm = (string)($_POST['password_confirm'] ?? '');

        if (!isStrongPassword($password)) {
            $error = 'Пароль должен быть не короче 8 символов и содержать буквы и цифры.';
        } elseif ($password !== $passwordConfirm) {
            $error = 'Пароли не совпадают.';
        } else {
            $update = db()->prepare(
                'UPDATE users
                 SET password_hash = :password_hash,
                     password_reset_token = NULL,
                     password_reset_expires_at = NULL,
                     updated_at = NOW()
                 WHERE id = :id'
            );
            $update->execute([
                'password_hash' => password_hash($password, PASSWORD_DEFAULT),
                'id' => (int)$user['id'],
            ]);

            flash('success', 'Пароль обновлён. Теперь можно войти с новым паролем.');
            redirect('/index.php?auth=login');
        }
    }
} catch (Throwable $e) {
    error_log('Reset password error: ' . $e->getMessage());
    flash('error', 'Не удалось обновить пароль.');
    redirect('/forgot_password.php');
}
?>
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Новый пароль | Академия</title>
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>
<main class="container auth-page">
  <?php if ($error): ?>
    <div class="alert error"><?= htmlspecialchars($error) ?></div>
  <?php endif; ?>

  <article class="card auth-page-card">
    <h1>Новый пароль</h1>
    <p class="muted">Придумайте новый пароль и подтвердите его.</p>

    <form method="post" class="form">
      <input type="hidden" name="token" value="<?= htmlspecialchars($token) ?>" />

      <label>
        Новый пароль
        <input type="password" name="password" minlength="8" required />
      </label>

      <label>
        Подтвердите пароль
        <input type="password" name="password_confirm" minlength="8" required />
      </label>

      <button type="submit">Сохранить новый пароль</button>
    </form>
  </article>
</main>
<?php require __DIR__ . '/footer.php'; ?>
</body>
</html>
