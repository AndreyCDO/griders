<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

$heroHtml = contentHtml(
    'index-hero-copy',
    '<p>Крипторг Академия — простой старт в криптотрейдинге</p><p>Освойте торговлю на Cryptorg, научитесь работать с ботами и улучшайте свои результаты — даже без опыта.</p><p>Готовые стратегии, удобные инструменты и бесплатное обучение в одном месте.</p>'
);
$homeHtml = contentHtml(
    'home-main-content',
    '<h2>Крипторг Академия — обучение торговле на Cryptorg и криптотрейдингу с нуля</h2><p>Рады приветствовать вас на сайте Крипторг Академии.</p>'
);
$consentHtml = contentHtml('personal-data-consent');
$currentUser = currentUser();

$success = flash('success');
$error = flash('error');
$authView = (string)($_GET['auth'] ?? '');
$allowedViews = ['login', 'register'];
$activeAuthView = in_array($authView, $allowedViews, true) ? $authView : '';

if ($activeAuthView === '' && ($success || $error)) {
    $activeAuthView = 'register';
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
  <title>Крипторг Академия | ctgacademy.ru</title>
  <meta name="description" content="Крипторг Академия — обучение торговле на Cryptorg и криптотрейдингу с нуля." />
  <?php require __DIR__ . '/site_head_assets.php'; ?>
</head>
<body>
<?php require __DIR__ . '/site_header.php'; ?>

<header class="hero">
  <div class="container">
    <h1>Крипторг Академия</h1>
    <div class="hero-copy">
      <?= $heroHtml ?>
    </div>

  </div>
</header>

<main class="container">
  <?php if ($success): ?>
    <div class="alert success"><?= htmlspecialchars($success) ?></div>
  <?php endif; ?>

  <?php if ($error): ?>
    <div class="alert error"><?= htmlspecialchars($error) ?></div>
  <?php endif; ?>

  <?php if (!$currentUser): ?>
    <section
      id="authSection"
      class="auth-grid<?= $activeAuthView !== '' ? '' : ' is-hidden' ?>"
      aria-label="Авторизация"
    >
      <article id="registerCard" class="card auth-card" <?= $activeAuthView === 'login' ? 'hidden' : '' ?>>
        <h2>Регистрация</h2>
        <form action="/register.php" method="post" class="form">
          <label>
            Email
            <input type="email" name="email" required />
          </label>
          <label>
            Пароль
            <input type="password" name="password" minlength="8" pattern="(?=.*[A-Za-zА-Яа-яЁё])(?=.*\d).{8,}" title="Минимум 8 символов, обязательно буквы и цифры" required />
          </label>
          <p class="muted form-hint">Минимум 8 символов, обязательно буквы и цифры.</p>
          <label class="consent-check">
            <input type="checkbox" name="consent_personal_data" value="1" required />
            <span class="consent-text">
              Я даю согласие на обработку моих персональных данных в соответствии с Политикой конфиденциальности и условиями настоящего
              <button type="button" class="consent-inline-link" id="openConsentModal">Согласия</button>.
            </span>
          </label>
          <button type="submit">Создать аккаунт</button>
        </form>
      </article>

      <article id="loginCard" class="card auth-card" <?= $activeAuthView === 'register' ? 'hidden' : '' ?>>
        <h2>Вход</h2>
        <form action="/login.php" method="post" class="form">
          <label>
            Email
            <input type="email" name="email" required />
          </label>
          <label>
            Пароль
            <input type="password" name="password" required />
          </label>
          <a class="login-help" href="/forgot_password.php">Забыли пароль?</a>
          <button type="submit">Войти</button>
        </form>
      </article>
    </section>
  <?php endif; ?>

  <section class="socials" aria-label="Социальные сети">
    <div class="card">
      <h2>Мы в социальных сетях</h2>
      <ul>
        <li><a href="https://vkvideo.ru/@club235515025" target="_blank" rel="noopener noreferrer">ВК</a></li>
        <li><a href="https://www.youtube.com/@limonovka" target="_blank" rel="noopener noreferrer">YouTube</a></li>
        <li><a href="https://rutube.ru/channel/3811875" target="_blank" rel="noopener noreferrer">RuTube</a></li>
        <li><a href="https://t.me/+_MFj8nYmcRAwN2Vi" target="_blank" rel="noopener noreferrer">Telegram</a></li>
      </ul>
    </div>
  </section>

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

  <a href="https://cryptorg.net/?ref=101002" target="_blank" rel="noopener noreferrer" class="hero-banner">
    <img src="/images/cryptorg_banner_3.jpg" alt="Cryptorg">
  </a>

  <section class="home-content">
    <div class="card content-card">
      <?= $homeHtml ?>
    </div>
  </section>
</main>

<div id="consentModal" class="modal-overlay" hidden>
  <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="consentModalTitle">
    <div class="modal-head">
      <h2 id="consentModalTitle">Согласие на обработку персональных данных</h2>
      <button type="button" class="modal-close" id="closeConsentModal" aria-label="Закрыть">&times;</button>
    </div>
    <div class="modal-body">
      <?php if ($consentHtml !== ''): ?>
        <?= $consentHtml ?>
      <?php else: ?>
        <p class="muted">Текст согласия пока не загружен в базу данных.</p>
      <?php endif; ?>
    </div>
  </div>
</div>

<?php require __DIR__ . '/footer.php'; ?>

<script>
  const authSection = document.getElementById("authSection");
  const loginCard = document.getElementById("loginCard");
  const registerCard = document.getElementById("registerCard");
  const consentModal = document.getElementById("consentModal");
  const openConsentModal = document.getElementById("openConsentModal");
  const closeConsentModal = document.getElementById("closeConsentModal");

  if (authSection && loginCard && registerCard) {
    const openAuth = (view) => {
      authSection.classList.remove("is-hidden");
      loginCard.hidden = view !== "login";
      registerCard.hidden = view !== "register";
      authSection.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    const initialView = "<?= $activeAuthView ?>";
    if (initialView === "login" || initialView === "register") {
      openAuth(initialView);
    }
  }

  if (consentModal && openConsentModal && closeConsentModal) {
    const hideConsentModal = () => {
      consentModal.hidden = true;
      document.body.classList.remove("modal-open");
    };

    openConsentModal.addEventListener("click", () => {
      consentModal.hidden = false;
      document.body.classList.add("modal-open");
    });

    closeConsentModal.addEventListener("click", hideConsentModal);

    consentModal.addEventListener("click", (event) => {
      if (event.target === consentModal) {
        hideConsentModal();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !consentModal.hidden) {
        hideConsentModal();
      }
    });
  }
</script>
</body>
</html>
