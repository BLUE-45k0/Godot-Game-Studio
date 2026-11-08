# Godot Game Studio

أداة سطر أوامر بلغة Python: تكتب فكرة لعبة في سطر أو سطرين، فيشغّل فريقٌ من وكلاء الذكاء الاصطناعي
(مصمم، ومصمم مستويات، وفن، وصوت، ومبرمج Godot، وQA، وخمسة مجرّبين) ويُنتج مشروع **Godot 4** ثنائي الأبعاد.

## المتطلبات
- Python 3.11 أو أحدث
- مفتاح Anthropic API واسم نموذج متاح في حسابك
- (موصى به) ملف Godot 4 التنفيذي لفحص حقيقي؛ بدونه يكتفي QA بالفحص الثابت ويصرّح بذلك في التقرير

## التثبيت
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # ثم املأ ANTHROPIC_API_KEY و GGS_MODEL
cp config.example.toml config.toml
pytest -q                       # اختبارات الأداة نفسها (لا تحتاج مفتاحاً ولا شبكة)
```
راجع `config.toml`: مسار Godot (`godot.executable_path`) وإصداره (`godot.target_version`)، وروابط حزم Kenney (اختيارية)، وعدد محاولات الإصلاح.

## التشغيل
```bash
python main.py "لعبة منصات عن قطة فضاء"
python main.py "space cat platformer" --godot-path /path/to/godot --overwrite -v
```

## المخرجات (`output/<اسم_المشروع>/`)
- `project.godot` و`scenes/` و`scripts/` و`assets/` (sprites وaudio مولّدة إجرائياً أو من حزم تضبطها بنفسك)
- `reports/qa_report.json|md` (و`_final` بعد جولة التحسين) و`playtest_<persona>.json` و`playtest_summary.json` و`run_summary.json`
- `studio_docs/`: مخرجات كل وكيل (التصميم، والمستويات، والفن، والصوت)

## تدفق العمل
مصمم اللعبة ← مصمم المستويات ← (الفن والصوت بالتوازي) ← مبرمج Godot ← QA (فحص ثابت + Godot headless إن وُجد + إصلاح تلقائي)
← خمسة مجرّبين ← Aggregator ← جولة تحسين واحدة ← QA نهائي.

## كيف يعمل QA
1. فحص ثابت دائماً: مسارات `res://`، وترتيب العقد والاتصالات في `.tscn`، ومعرّفات الموارد، وأفعال الإدخال، وإشارات غير مُطلَقة أو غير موصولة، وأنماط Godot 3، والإزاحة المختلطة.
2. إن وُجد Godot: `--headless --import` ثم `--check-only` لكل سكربت.
3. حلقة إصلاح تلقائي: الأخطاء الحقيقية تُرسل للمبرمج (حتى `qa.max_fix_attempts`)، وما لم يُحل يُكتب في التقرير.

## حدود معروفة
- المجرّبون الخمسة **محاكاة لغوية** تقرأ التصميم والكود؛ لا أحد يلعب اللعبة فعلياً.
- لا يوجد اختبار تشغيل فعلي للعبة؛ نجاح الفحص يعني أن المشروع يُحلَّل ويُستورد، لا أنه ممتع أو خالٍ من أخطاء وقت التشغيل.
- الأصول الإجرائية بسيطة (أشكال وموجات أساسية).
- الأداة تستهدف ألعاباً صغيرة ثنائية الأبعاد.
- أكواد المفاتيح في `tools/godot_writer.py` (`KEYCODES`) كُتبت من تعداد `Key` في Godot 4؛ تحقق منها مرة واحدة في Input Map.

## الرفع على GitHub
```bash
git init
git add .
git status            # تأكد أن .env و config.toml و output/ غير موجودة في القائمة
git commit -m "Initial commit: Godot Game Studio"
git branch -M main
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```
أو بوجود GitHub CLI: `gh repo create godot-game-studio --private --source=. --push`.

## التراخيص
حزم Kenney (إن استخدمتها) بترخيص CC0 حسب موقعها، وتحقق من ترخيص كل حزمة تضيف رابطها. أضف ملف `LICENSE` للمشروع حسب اختيارك قبل نشره علناً.
