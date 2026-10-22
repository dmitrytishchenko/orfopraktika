package ru.orfopraktika.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * ОрфоПрактика: тренажёр из assets/index.html во WebView.
 * Работает без интернета. Через объект OrfoAndroid странице доступны голос Android
 * для мини-диктанта и сохранение резервных копий и отчётов в выбранное место.
 */
public class MainActivity extends Activity {
    private static final String HOME = "file:///android_asset/index.html";
    private static final int REQUEST_OPEN_FILE = 1;
    private static final int REQUEST_SAVE_FILE = 2;

    private WebView web;
    private TextToSpeech tts;
    private volatile String ttsStatus = "loading";
    private ValueCallback<Uri[]> openCallback;
    private String pendingContent;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(0xFF176A58);

        web = new WebView(this);
        web.setFitsSystemWindows(true);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("file".equals(uri.getScheme())) return false;
                // Внешние ссылки открываем в браузере, а не внутри тренажёра.
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (openCallback != null) openCallback.onReceiveValue(null);
                openCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                try {
                    startActivityForResult(intent, REQUEST_OPEN_FILE);
                    return true;
                } catch (ActivityNotFoundException e) {
                    openCallback = null;
                    return false;
                }
            }
        });

        web.addJavascriptInterface(new Bridge(), "OrfoAndroid");

        tts = new TextToSpeech(this, status -> {
            if (status != TextToSpeech.SUCCESS) {
                ttsStatus = "error";
                return;
            }
            int result = tts.setLanguage(Locale.forLanguageTag("ru-RU"));
            ttsStatus = result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED
                    ? "missing" : "ready";
        });

        setContentView(web);
        if (state == null || web.restoreState(state) == null) web.loadUrl(HOME);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        // Страница сама решает: закрыть диалог, вернуться на главную или разрешить выход.
        web.evaluateJavascript("window.orfoBack ? window.orfoBack() : false", result -> {
            if (!"true".equals(result)) finish();
        });
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        Uri uri = resultCode == RESULT_OK && data != null ? data.getData() : null;
        if (requestCode == REQUEST_OPEN_FILE) {
            if (openCallback != null) {
                openCallback.onReceiveValue(uri != null ? new Uri[]{uri} : null);
                openCallback = null;
            }
        } else if (requestCode == REQUEST_SAVE_FILE) {
            String content = pendingContent;
            pendingContent = null;
            if (uri == null || content == null) return;
            try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                if (out == null) throw new IOException("Нет доступа к файлу");
                out.write(content.getBytes(StandardCharsets.UTF_8));
                Toast.makeText(this, "Файл сохранён", Toast.LENGTH_SHORT).show();
            } catch (IOException | SecurityException e) {
                Toast.makeText(this, "Не удалось сохранить файл", Toast.LENGTH_LONG).show();
            }
        }
    }

    @Override
    protected void onDestroy() {
        if (tts != null) {
            tts.stop();
            tts.shutdown();
        }
        if (web != null) web.destroy();
        super.onDestroy();
    }

    /** Методы, которые видит страница как window.OrfoAndroid. */
    private class Bridge {
        @JavascriptInterface
        public String ttsStatus() {
            return ttsStatus;
        }

        @JavascriptInterface
        public void speak(String text, float rate) {
            runOnUiThread(() -> {
                if (tts == null || !"ready".equals(ttsStatus)) return;
                tts.setSpeechRate(rate);
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "orfo");
            });
        }

        @JavascriptInterface
        @SuppressWarnings("deprecation")
        public void saveFile(String name, String mime, String content) {
            runOnUiThread(() -> {
                pendingContent = content;
                Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
                intent.putExtra(Intent.EXTRA_TITLE, name);
                try {
                    startActivityForResult(intent, REQUEST_SAVE_FILE);
                } catch (ActivityNotFoundException e) {
                    pendingContent = null;
                    Toast.makeText(MainActivity.this, "Не удалось открыть сохранение файла", Toast.LENGTH_LONG).show();
                }
            });
        }
    }
}
