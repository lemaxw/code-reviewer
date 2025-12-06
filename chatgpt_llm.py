import logging
import os
import openai
from openai import OpenAIError, BadRequestError  # Ensure proper imports
from llm_interface import LLMInterface, ModelResult
from config import LOG_CHAR_LIMIT

class ChatGPTLLM(LLMInterface):
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required for ChatGPT")
        self.client = openai.OpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.1-codex-max")

    def answer(self, system_prompt: str, user_prompt: str, content: str) -> ModelResult:
        """Generate a JSON response for the given prompts and content."""
        logging.debug(
            f"ChatGPT Request:\nModel: {self.model}\nSystem Prompt: {system_prompt[:LOG_CHAR_LIMIT]}..."
            f"\nUser Prompt: {user_prompt[:LOG_CHAR_LIMIT]}...\nContent: {content[:LOG_CHAR_LIMIT]}... (truncated)"
        )

        try:
            prompt_content = user_prompt + "\n" + content if user_prompt else content
            response = self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_content},
                ]
            )
            # responses.create returns output as text content; prefer convenience field when present
            raw_response = getattr(response, "output_text", None)
            if not raw_response and getattr(response, "output", None):
                first_output = response.output[0]
                if getattr(first_output, "content", None):
                    first_piece = first_output.content[0]
                    raw_response = getattr(first_piece, "text", "") or ""
            raw_response = raw_response.strip() if raw_response else ""

            usage = getattr(response, "usage", None)
            total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
            # Support both new (input/output) and legacy prompt/completion names
            prompt_tokens = 0
            completion_tokens = 0
            if usage:
                prompt_tokens = getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", 0))
                completion_tokens = getattr(usage, "output_tokens", getattr(usage, "completion_tokens", 0))

            logging.debug(f"Raw Response:\n{raw_response[:LOG_CHAR_LIMIT]}... (truncated)")
            return ModelResult(
                response=raw_response,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except (BadRequestError, OpenAIError) as e:
            error_message = str(e)
            if "context length" in error_message or "context_length_exceeded" in error_message or 'Request too larg' in error_message:
                logging.warning("Request too long for model context window.")
                return ModelResult(response="Long_Request", total_tokens=0, prompt_tokens=0, completion_tokens=0)
            else:
                logging.error(f"ChatGPT Error: {error_message}")
                return None            
        except Exception as e:
            print(f"Error communicating with ChatGPT API: {str(e)}")
            return None
