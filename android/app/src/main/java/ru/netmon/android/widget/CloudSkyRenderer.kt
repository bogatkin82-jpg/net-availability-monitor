package ru.netmon.android.widget

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Реалистичная сцена «облачно» 1.5:1 (расширена влево):
 * мягкие облака, за ними солнце днём или луна с кратерами и реальной фазой ночью.
 */
object CloudSkyRenderer {

    private const val W = 384
    private const val H = 256

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
    private val craters = listOf(
        Triple(-0.28f, -0.22f, 0.16f),
        Triple(0.10f, -0.36f, 0.10f),
        Triple(-0.05f, 0.20f, 0.13f),
        Triple(0.34f, 0.10f, 0.09f),
        Triple(-0.42f, 0.28f, 0.08f),
    )

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var cacheKey: Int = -1
    private var cache: Bitmap? = null

    fun render(isDay: Boolean, frame: Int): Bitmap {
        val phase = moonPhase()
        val phaseBucket = (phase * 64.0).toInt().coerceIn(0, 63)
        val key = (phaseBucket shl 8) or ((if (isDay) 1 else 0) shl 7) or (frame % 128)
        cache?.let { if (cacheKey == key) return it }

        val bitmap = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR)
        drawCloudLayer(canvas, farClouds, frame)
        drawCloudLayer(canvas, nearClouds, frame)
        if (isDay) drawSun(canvas) else drawMoon(canvas, phase)
        cacheKey = key
        cache = bitmap
        return bitmap
    }

    private fun drawSun(canvas: Canvas) {
        val cx = W * 0.77f
        val cy = H * 0.17f
        val r = H * 0.205f

        paint.shader = RadialGradient(
            cx, cy, r * 3.4f,
            intArrayOf(Color.argb(150, 255, 214, 140), Color.argb(0, 255, 214, 140)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r * 3.4f, paint)
        paint.shader = null

        // лучи
        paint.color = Color.argb(55, 255, 232, 170)
        paint.strokeWidth = H * 0.014f
        paint.strokeCap = Paint.Cap.ROUND
        for (i in 0 until 12) {
            val a = i * PI / 6.0
            val r1 = r * 1.35f
            val r2 = r * 1.75f
            canvas.drawLine(
                cx + cos(a).toFloat() * r1, cy + sin(a).toFloat() * r1,
                cx + cos(a).toFloat() * r2, cy + sin(a).toFloat() * r2,
                paint,
            )
        }
        paint.strokeCap = Paint.Cap.BUTT

        paint.shader = RadialGradient(
            cx, cy, r,
            intArrayOf(Color.rgb(255, 250, 220), Color.rgb(255, 214, 120)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r, paint)
        paint.shader = null
        paint.color = Color.argb(190, 255, 255, 245)
        canvas.drawCircle(cx - r * 0.18f, cy - r * 0.18f, r * 0.42f, paint)
    }

    private fun drawMoon(canvas: Canvas, phase: Double) {
        val cx = W * 0.77f
        val cy = H * 0.17f
        val r = H * 0.205f
        val fraction = ((1.0 - cos(2.0 * PI * phase)) / 2.0).toFloat().coerceIn(0f, 1f)
        val waxing = phase < 0.5

        // окно чистого ночного неба
        paint.shader = RadialGradient(
            cx, cy, r * 2.4f,
            intArrayOf(Color.argb(200, 5, 11, 24), Color.argb(0, 5, 11, 24)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r * 2.4f, paint)
        paint.shader = null

        paint.shader = RadialGradient(
            cx, cy, r * 2.0f,
            intArrayOf(Color.argb(90, 205, 220, 255), Color.argb(0, 205, 220, 255)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r * 2.0f, paint)
        paint.shader = null

        val layer = canvas.saveLayer(cx - r, cy - r, cx + r, cy + r, null)
        // лунный диск с потемнением к краю
        paint.shader = RadialGradient(
            cx, cy, r,
            intArrayOf(Color.rgb(246, 244, 234), Color.rgb(196, 194, 184)),
            null, Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, r, paint)
        paint.shader = null

        // кратеры (видны на освещённой части)
        paint.color = Color.argb(70, 120, 120, 112)
        for ((dx, dy, rr) in craters) {
            canvas.drawCircle(cx + dx * r, cy + dy * r, rr * r, paint)
        }

        // фазовый терминатор: эллиптическая тень
        if (fraction < 0.995f) {
            val rx = r * (1f - fraction)
            val shadowCx = if (waxing) cx - r * fraction else cx + r * fraction
            val shadow = Path().apply {
                addOval(RectF(shadowCx - rx, cy - r, shadowCx + rx, cy + r), Path.Direction.CW)
            }
            paint.xfermode = PorterDuffXfermode(PorterDuff.Mode.CLEAR)
            canvas.drawPath(shadow, paint)
            paint.xfermode = null
        }

        // земное сияние и контур, чтобы тонкий серп не терялся
        paint.color = Color.argb(115, 225, 232, 245)
        canvas.drawCircle(cx, cy, r, paint)
        paint.color = Color.argb(185, 235, 240, 250)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1.8f
        canvas.drawCircle(cx, cy, r, paint)
        paint.style = Paint.Style.FILL
        canvas.restoreToCount(layer)
    }

    private fun drawCloudLayer(canvas: Canvas, clouds: List<Cloud>, frame: Int) {
        for (cloud in clouds) {
            val width = H * 0.80f * cloud.scale
            val xNorm = wrap(cloud.x + frame * cloud.speed / 60f)
            drawCloud(canvas, xNorm * W, cloud.y * H, width, cloud.alpha)
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
}
