/**
 * AudioWorklet processor that converts float32 audio samples to int16 PCM.
 *
 * This runs in a separate audio rendering thread (not the main thread),
 * so it has minimal impact on UI performance.
 */
const MAX_INT16 = 32767;

class AudioProcessor extends AudioWorkletProcessor {
    process(inputs) {
        const input = inputs[0];
        if (!input || !input[0]) return true;

        const float32 = input[0];
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        this.port.postMessage({ audio_data: int16.buffer }, [int16.buffer]);
        return true;
    }
}

registerProcessor("audio-processor", AudioProcessor);
