//=============================================================================
// Translator_AutoWrap.js
//=============================================================================
/*:
 * @target MZ
 * @plugindesc Страховочный перенос строк по реальной ширине окна сообщения.
 * @author RPG Maker Translator
 *
 * @param maxExtraPages
 * @text Предохранитель
 * @desc Во сколько раз текст может вырасти, прежде чем плагин отключится.
 * @type number
 * @min 0
 * @max 20
 * @default 6
 *
 * @help
 * Translator_AutoWrap.js
 * ----------------------
 * Основную вёрстку делает сама утилита перевода: она заранее меряет текст
 * шрифтом игры, переносит его по ширине окна и раскладывает по окнам. Этот
 * плагин — страховка на случай, когда офлайн-расчёт разошёлся с реальностью:
 * игрок сменил шрифт, плагин поменял ширину окна или размер шрифта, включён
 * портрет там, где его не ждали.
 *
 * Что он делает:
 *   • меряет каждую строку методами самого движка (this.textWidth), то есть
 *     ровно тем шрифтом и размером, которыми она будет нарисована;
 *   • переносит по словам, не разрывая управляющие коды (\C[n], \I[n], …);
 *   • НЕ разбивает сообщение на окна: это делает офлайн-проход утилиты, и он
 *     режет по границам предложений, а не по счёту строк.
 *
 * Плагин не трогает сообщения, которые и так помещаются, поэтому на уже
 * свёрстанном тексте он не срабатывает вообще.
 *
 * Совместимость: MV и MZ. Регистрируется последним, чтобы видеть окончательные
 * размеры окна после всех остальных плагинов.
 */
/*:ru
 * @target MZ
 * @plugindesc Страховочный перенос строк по реальной ширине окна сообщения.
 * @author RPG Maker Translator
 */

var Imported = Imported || {};
Imported.Translator_AutoWrap = true;

(function () {
    "use strict";

    var PLUGIN_NAME = "Translator_AutoWrap";
    var MAX_EXTRA_PAGES = 6;

    try {
        if (typeof PluginManager !== "undefined" && PluginManager.parameters) {
            var params = PluginManager.parameters(PLUGIN_NAME);
            if (params && params.maxExtraPages) {
                MAX_EXTRA_PAGES = Number(params.maxExtraPages) || MAX_EXTRA_PAGES;
            }
        }
    } catch (e) {
        // Параметры не обязательны — работаем со значением по умолчанию.
    }

    // Управляющие коды после convertEscapeCharacters приходят как \x1b + буквы.
    // Ширины они не имеют (кроме иконки), поэтому при измерении убираются.
    var CONTROL_RE = /\x1b[A-Za-z]+(\[\d+\])?/g;

    function iconWidth() {
        if (typeof ImageManager !== "undefined" && ImageManager.iconWidth) {
            return ImageManager.iconWidth + 4;   // MZ
        }
        if (typeof Window_Base !== "undefined" && Window_Base._iconWidth) {
            return Window_Base._iconWidth + 4;   // MV
        }
        return 36;
    }

    // ── Измерение ───────────────────────────────────────────────────────────

    function measure(win, text) {
        if (!text) {
            return 0;
        }
        var icons = 0;
        var plain = text.replace(/\x1bI\[\d+\]/g, function () {
            icons += 1;
            return "";
        });
        plain = plain.replace(CONTROL_RE, "");
        var width = 0;
        try {
            width = win.textWidth(plain);
        } catch (e) {
            // На всякий случай: если движок не дал померить, считаем грубо.
            width = plain.length * (win.contents ? win.contents.fontSize : 28) * 0.5;
        }
        return width + icons * iconWidth();
    }

    function availableWidth(win) {
        var width = win.innerWidth !== undefined
            ? win.innerWidth
            : win.contentsWidth();
        var offset = 0;
        try {
            // newLineX учитывает портрет: с ним текста влезает на 168 px меньше.
            offset = win.newLineX ? win.newLineX({ text: "", index: 0 }) : 0;
        } catch (e) {
            try {
                offset = win.newLineX ? win.newLineX() : 0;
            } catch (e2) {
                offset = 0;
            }
        }
        return Math.max(64, width - (offset || 0));
    }

    // ── Перенос одной строки ────────────────────────────────────────────────

    // Токен = управляющий код вместе со следующим за ним словом, либо слово,
    // либо одиночный CJK-символ. Код никогда не отрывается от своего слова —
    // иначе цвет применился бы к пустому концу строки.
    var TOKEN_RE = /(?:\x1b[A-Za-z]+(?:\[\d+\])?)+|[\u3000-\u9fff\uff00-\uffef]|[^\s\u3000-\u9fff\uff00-\uffef]+|\s+/g;

    function tokenize(line) {
        var raw = line.match(TOKEN_RE) || [];
        var tokens = [];
        var pending = "";
        for (var i = 0; i < raw.length; i++) {
            var piece = raw[i];
            if (piece.charCodeAt(0) === 0x1b) {
                pending += piece;
                continue;
            }
            tokens.push(pending + piece);
            pending = "";
        }
        if (pending) {
            if (tokens.length) {
                tokens[tokens.length - 1] += pending;
            } else {
                tokens.push(pending);
            }
        }
        return tokens;
    }

    function wrapLine(win, line, limit) {
        if (measure(win, line) <= limit) {
            return [line];
        }
        var tokens = tokenize(line);
        var lines = [];
        var current = "";
        for (var i = 0; i < tokens.length; i++) {
            var candidate = current + tokens[i];
            if (current && measure(win, rtrim(candidate)) > limit) {
                lines.push(rtrim(current));
                current = ltrim(tokens[i]);
            } else {
                current = candidate;
            }
        }
        if (rtrim(current)) {
            lines.push(rtrim(current));
        }
        return lines.length ? lines : [line];
    }

    function rtrim(s) { return s.replace(/\s+$/, ""); }
    function ltrim(s) { return s.replace(/^\s+/, ""); }

    // ── Основной хук ────────────────────────────────────────────────────────

    function maxLines(win) {
        try {
            if (win.numVisibleRows) {
                return win.numVisibleRows();
            }
        } catch (e) { /* ниже — запасной вариант */ }
        return 4;
    }

    function reflow(win, text) {
        if (!text || text.indexOf("\n") < 0 && measure(win, text) <= availableWidth(win)) {
            return text;   // и так помещается — не трогаем
        }
        var limit = availableWidth(win);
        var rows = maxLines(win);
        var source = String(text).split("\n");
        var out = [];
        var fits = true;

        for (var i = 0; i < source.length; i++) {
            var wrapped = wrapLine(win, source[i], limit);
            if (wrapped.length > 1) {
                fits = false;
            }
            for (var j = 0; j < wrapped.length; j++) {
                out.push(wrapped[j]);
            }
        }

        if (fits) {
            return text;   // ширина в порядке, вмешиваться не во что
        }
        if (out.length > rows * (MAX_EXTRA_PAGES + 1)) {
            // Что-то пошло не так (чужой плагин сузил окно до нуля) —
            // лучше показать исходный текст, чем простыню из сотен строк.
            return text;
        }
        // Разбивку на окна делает офлайн-проход утилиты: он режет по границам
        // предложений. Здесь только чиним ширину — переполнение по высоте MZ
        // разложит сам (Window_Message.needsNewPage).
        return out.join("\n");
    }

    // convertEscapeCharacters уже подставил \N[n] и \V[n] реальными значениями,
    // поэтому здесь измерение совпадает с тем, что увидит игрок.
    var _convert = Window_Message.prototype.convertEscapeCharacters;
    Window_Message.prototype.convertEscapeCharacters = function (text) {
        var converted = _convert.call(this, text);
        try {
            return reflow(this, converted);
        } catch (e) {
            // Любая неожиданность не должна ломать показ сообщения.
            if (typeof console !== "undefined" && console.warn) {
                console.warn(PLUGIN_NAME + ": " + e);
            }
            return converted;
        }
    };
})();
