import logging
import os
import anthropic
from llm_interface import LLMInterface, ModelResult
from config import LOG_CHAR_LIMIT


class ClaudeLLM(LLMInterface):
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required for Claude")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")

    def answer(self, system_prompt: str, user_prompt: str, content: str) -> ModelResult:
        """Generate a JSON response for the given prompts and content."""
        logging.debug(
            f"Claude Request:\nModel: {self.model}\nSystem Prompt: {system_prompt[:LOG_CHAR_LIMIT]}..."
            f"\nUser Prompt: {user_prompt[:LOG_CHAR_LIMIT]}...\nContent: {content[:LOG_CHAR_LIMIT]}... (truncated)"
        )

        try:
            prompt_content = user_prompt + "\n" + content if user_prompt else content
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": prompt_content},
                ],
            )

            raw_response = response.content[0].text.strip() if response.content else ""

            usage = response.usage
            prompt_tokens = usage.input_tokens
            completion_tokens = usage.output_tokens
            total_tokens = prompt_tokens + completion_tokens

            logging.debug(f"Raw Response:\n{raw_response[:LOG_CHAR_LIMIT]}... (truncated)")
            return ModelResult(
                response=raw_response,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except anthropic.BadRequestError as e:
            error_message = str(e)
            if "too long" in error_message or "too many tokens" in error_message or "context" in error_message.lower():
                logging.warning("Request too long for model context window.")
                return ModelResult(response="Long_Request", total_tokens=0, prompt_tokens=0, completion_tokens=0)
            logging.error(f"Claude BadRequestError: {error_message}")
            return None
        except anthropic.APIError as e:
            logging.error(f"Claude API Error: {str(e)}")
            return None
        except Exception as e:
            logging.error(f"Error communicating with Claude API: {str(e)}")
            return None
