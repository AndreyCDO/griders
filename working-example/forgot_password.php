<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

if (currentUser()) {
    redirect('/dashboard.php');
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $email = trim((string)($_POST['email'] ?? ''));

    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        flash('error', 'Укажите корректный email.');
        redirect('/forgot_password.php');
    }

    try {
        $stmt = db()->prepare('SELECT id, verified_at FROM users WHERE email = :email LIMIT 1');
        $stmt->execute(['email' => $email]);
        $user = $stmt->fetch();

        if ($user && $user['verified_at']) {
            $token = bin2hex(random_bytes(32));

            $update = db()->prepare(
                'UPDATE users
                 SET password_reset_token = :token,
                     password_reset_expires_at = DATE_ADD(NOW(), INTERVAL 1 HOUR),
                     updated_at = NOW()
                 WHERE id = :id'
            );
            $update->execute([
                'token' => $token,
                'id' => (int)$user['id'],
            ]);

            $resetLink = APP_URL . '/reset_password.php?token=' . urlencode($token);
            $subject = 'Восстановление пароля — Академия Криптотрейдинга';
            $message = "Для сброса пароля перейдите по ссылке: {$resetLink}\n\nСсылка действует 1 час.";
            sendAppMail($email, $subject, $message);
        }

        flash('success', 'Если такой email найден, мы отправили ссылку для восстановления пароля.');
        redirect('/forgot_password.php');
    } catch (Throwable $e) {
        error_log('Forgot password error: ' . $e->getMessage());
        flash('error', 'Не удалось обработать запрос на восстановление пароля.');
        redirect('/forgot_password.php');
    }
}

$success = flash('success');
$error = flash('error');
?>
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Восстановление пароля | Академия</title>
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>
<main class="container auth-page">
  <?php if ($success): ?>
    <div class="alert success"><?= htmlspecialchars($success) ?></div>
  <?php endif; ?>

  <?php if ($error): ?>
    <div class="alert error"><?= htmlspecialchars($error) ?></div>
  <?php endif; ?>

  <article class="card auth-page-card">
    <a class="lesson-back" href="/index.php?auth=login">Назад ко входу</a>
    <h1>Восстановление пароля</h1>
    <p class="muted">Введите email, и мы отправим ссылку для смены пароля.</p>

    <form method="post" class="form">
      <label>
        Email
        <input type="email" name="email" required />
      </label>
      <button type="submit">Отправить ссылку</button>
    </form>
  </article>
</main>
<?php require __DIR__ . '/footer.php'; ?>
</body>
</html>
