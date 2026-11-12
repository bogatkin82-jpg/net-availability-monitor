package ru.netmon.android.widget

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.Shader
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor

/**
 * Реалистичная сцена «облачно»: мягкие облака из радиальных градиентов,
 * за ними солнце днём или луна с реальной фазой ночью.
 */
object CloudSkyRenderer {

    private const val SIZE = 256

    private data class Cloud(
        val x: Float,
        val y: Float,
        val scale: Float,
        val speed: Float,
        val alpha: Float,
    )

    private val farClouds = listOf(
        Cloud(0.10f, 0.50f, 0.65f, 0.28f, 0.11f),
        Cloud(0.55f, 0.55f, 0.75f, 0.20f, 0.10f),
        Cloud(0.82f, 0.47f, 0.55f, 0.33f, 0.09f),
    )
    private val nearClouds = listOf(
        Cloud(0.30f, 0.72f, 0.95f, 0.55f, 0.18f),
        Cloud(0.78f, 0.68f, 0.80f, 0.42f, 0.16f),
    )
    private val puffs = listOf(
        Triple(-0.40f, 0.06f, 0.34f),
        Triple(-0.16f, -0.10f, 0.43f),
        Triple(0.12f, -0.06f, 0.38f),
        Triple(0.38f, 0.08f, 0.30f),
        Triple(0.00f, 0.15f, 0.40f),
    )

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var cacheKey: Int = -1
    private var cache: Bitmap? = null

    fun render(isDay: Boolean, frame: Int): Bitmap {
        val phase = moonPhase()
        val phaseBucket = (phase * 64.0).toInt().coerceIn(0, 63)
        val key = (phaseBucket shl 8) or ((if (isDay) 1 else 0) shl 7) or (frame % 128)
        cache?.let { if (cacheKey == key) return it }

        val bitmap = Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR)
        // Светило рисуем поверх облаков с мягким ореолом, иначе тонкий серп
        // и солнце теряются в белых облаках.
        drawCloudLayer(canvas, farClouds, frame)
        drawCloudLayer(canvas, nearClouds, frame)
        if (isDay) drawSun(canvas) else drawMoon(canvas, phase)
        cacheKey = key
        cache = bitmap
        return bitmap
    }

    private fun drawSun(canvas: Canvas) {
        val cx = SIZE * 0.76f
        val cy = SIZE * 0.16f
        val r = SIZE * 0.140f
        paint.shader = RadialGradient(
            cx, cy, r * 3.2f,
            intArrayOf(Color.argb(120, 255, 226, 150), Color.argb(0, 255, 226, 150)),
            null, Shader.TileMode.CLAMP,
        )
        paint.alpha = 255
        canvas.drawCircle(cx, cy, r * 3.2f, paint)
        paint.shader = null
        paint.color = Color.rgb(255, 232, 170)
        canvas.drawCircle(cx, cy, r, paint)
        paint.color = Color.argb(90, 255, 255, 255)
        canvas.drawCircle(cx - r * 0.18f, cy - r * 0.18f, r * 0.55f, paint)
    }

    private fun drawMoon(canvas: Canvas, phase: Double) {
        val cx = SIZE * 0.76f
        val cy = SIZE * 0.16f
        val r = SIZE * 0.120f
        // окно чистого ночного неба вокруг луны — контраст с облаками
        paint.shader = RadialGradient(
            cx, cy, r * 2.6f,
            intArrayOf(Color.argb(205, 5, 11, 24), Color.argb(0, 5, 11, 24)),
            null, Shader.TileMode.CLAMP,
        )
        paint.alpha = 255
        canvas.drawCircle(cx, cy, r * 2.6f, paint)
        paint.shader = RadialGradient(
            cx, cy, r * 2.2f,
            intArrayOf(Color.argb(95, 210, 226, 255), Color.argb(0, 210, 226, 255)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r * 2.2f, paint)
        paint.shader = null

        val fraction = ((1.0 - cos(2.0 * PI * phase)) / 2.0).toFloat().coerceIn(0f, 1f)
        val waxing = phase < 0.5

        val layer = canvas.saveLayer(cx - r, cy - r, cx + r, cy + r, null)
        paint.color = Color.rgb(246, 244, 232)
        canvas.drawCircle(cx, cy, r, paint)

        // Терминатор: прямая граница тени, оставляющая освещённую часть.
        val terminatorX = if (waxing) {
            cx + r - 2f * r * fraction
        } else {
            cx - r + 2f * r * fraction
        }
        val shadow = Path().apply {
            if (waxing) {
                addRect(cx - r, cy - r, terminatorX, cy + r, Path.Direction.CW)
            } else {
                addRect(terminatorX, cy - r, cx + r, cy + r, Path.Direction.CW)
            }
        }
        paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.CLEAR)
        canvas.drawPath(shadow, paint)
        paint.xfermode = null
        // земное сияние: даже тонкий серп остаётся видимым
        paint.color = Color.argb(80, 235, 240, 255)
        canvas.drawCircle(cx, cy, r, paint)
        paint.color = Color.argb(160, 255, 255, 255)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.5f
        canvas.drawCircle(cx, cy, r, paint)
        paint.style = Paint.Style.FILL
        canvas.restoreToCount(layer)
    }

    private fun drawClouds(canvas: Canvas, frame: Int) {
        drawCloudLayer(canvas, farClouds, frame)
        drawCloudLayer(canvas, nearClouds, frame)
    }

    private fun drawCloudLayer(canvas: Canvas, clouds: List<Cloud>, frame: Int) {
        for (cloud in clouds) {
            val width = SIZE * 0.62f * cloud.scale
            val xNorm = wrap(cloud.x + frame * cloud.speed / 60f)
            val cx = xNorm * SIZE
            val cy = cloud.y * SIZE
            drawCloud(canvas, cx, cy, width, cloud.alpha)
        }
    }

    private fun drawCloud(canvas: Canvas, cx: Float, cy: Float, width: Float, alpha: Float) {
        for ((px, py, pr) in puffs) {
            val x = cx + px * width
            val y = cy + py * width
            val r = pr * width
            paint.shader = RadialGradient(
                x, y, r,
                intArrayOf(Color.argb((alpha * 255).toInt(), 255, 255, 255), Color.argb(0, 255, 255, 255)),
                null, Shader.TileMode.CLAMP,
            )
            paint.alpha = 255
            canvas.drawCircle(x, y, r, paint)
        }
        paint.shader = null
    }

    private fun wrap(value: Float): Float {
        var v = value % 1.35f
        if (v < -0.15f) v += 1.35f
        return v
    }

    /** Фаза луны 0..1 (0 — новолуние, 0.5 — полнолуние). */
    private fun moonPhase(): Double {
        val synodic = 29.530588853
        val knownNewMoonMs = 947_182_440_000L // 2000-01-06 18:14 UTC
        val ageDays = (System.currentTimeMillis() - knownNewMoonMs) / 86_400_000.0
        val age = ((ageDays % synodic) + synodic) % synodic
        return age / synodic
    }

    @Suppress("unused")
    private fun illuminatedFraction(phase: Double): Double =
        (1.0 - cos(2.0 * PI * phase)) / 2.0
}
